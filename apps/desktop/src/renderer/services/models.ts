import { uiGet, uiRemove, uiSet } from "@/lib/uiStorage";
import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

/**
 * 模型目录 + 组合 last-used（槽位编辑用目录；输入框选组合记 last_profile_id）。
 *
 * 槽位编辑以 `GET /v1/users/me/models` 为数据源；组合 CRUD 见
 * {@link import("@/services/llmModelProfiles")}。REST 类型由后端 OpenAPI 生成（`pnpm gen:types`）。
 */

type Schemas = components["schemas"];

/** 目录响应：账号默认、BYOK 是否已配置、可选模型清单。 */
export type ModelCatalog = Schemas["ModelCatalogResponse"];
/** 目录里的一个模型（可选或置灰）。 */
export type ModelCatalogItem = Schemas["ModelCatalogItem"];
/** 账号当前解析出的默认模型。 */
export type ModelCatalogCurrent = Schemas["ModelCatalogCurrent"];
/** 复用的单价卡（USD 每百万 token，字符串；金额从不用浮点）。 */
export type ModelPriceCard = Schemas["ModelPriceCard"];
/** 结构化不可选原因（前端自己渲染文案）。 */
export type ModelUnavailableReason = Schemas["ModelUnavailableReason"];

/** 已识别的模型能力标签（vision / tools / reasoning 的子集）。 */
export type ModelCapability = "vision" | "tools" | "reasoning";

/**
 * 模型选择键：产品身份为目录 `ref`（`@platform/{id}` / `@byok/{provider_id}/{id}`）。
 * 同一模型 id 可在多个 BYOK 服务商下重复出现（且平台再出现一次）。
 */
export type ModelOrigin = ModelCatalogItem["origin"];
export type ModelSelection = {
  id: string;
  origin: ModelOrigin;
  /** byok 行所属服务商 id（平台行为空）。旧存储可能缺省——匹配时回落到 (id, origin)。 */
  providerId?: string | null;
};

/** Product catalog identity: `@platform/{id}` or `@byok/{provider_id}/{id}`. */
export function catalogItemRef(
  item: Pick<ModelCatalogItem, "id" | "origin"> & {
    provider_id?: string | null;
    ref?: string;
  },
): string {
  if (typeof item.ref === "string" && item.ref.trim()) return item.ref.trim();
  if (item.origin === "platform" || !item.provider_id) {
    return `@platform/${item.id}`;
  }
  return `@byok/${item.provider_id}/${item.id}`;
}

/** Stable key for maps / MRU lists —— `(origin, providerId, id)` 三元组。 */
export function modelItemKey(
  item: Pick<ModelCatalogItem, "id" | "origin"> & {
    provider_id?: string | null;
    providerId?: string | null;
  },
): string {
  const providerId = item.provider_id ?? item.providerId ?? "";
  return `${item.origin}:${providerId}:${item.id}`;
}

/**
 * Resolve one catalog row by `(id, origin, provider_id)`。带 providerId 的 byok 选择精确匹配；
 * 无 providerId（平台 / 旧存储）时按 `(id, origin)` 匹配，优先可用行。
 */
export function findCatalogItem(
  models: ModelCatalogItem[],
  sel: ModelSelection,
): ModelCatalogItem | undefined {
  if (sel.origin === "byok" && sel.providerId) {
    const exact = models.find(
      (m) =>
        m.id === sel.id &&
        m.origin === "byok" &&
        m.provider_id === sel.providerId,
    );
    if (exact) return exact;
  }
  const matches = models.filter(
    (m) => m.id === sel.id && m.origin === sel.origin,
  );
  return matches.find((m) => m.available) ?? matches[0];
}

/** Whether this profile slot's catalog row lists ``vision``. */
export function slotHasCatalogVision(
  slot: {
    model: string;
    origin: ModelCatalogItem["origin"];
    provider_id?: string | null;
  },
  catalogModels: ModelCatalogItem[],
): boolean {
  const item = findCatalogItem(catalogModels, {
    id: slot.model,
    origin: slot.origin,
    providerId: slot.provider_id,
  });
  return (item?.capabilities ?? []).includes("vision");
}

/** Official vendor thinking-effort tokens for this slot's catalog row, or null. */
export function catalogReasoningEffort(
  slot:
    | {
        model: string;
        origin?: ModelCatalogItem["origin"];
        provider_id?: string | null;
      }
    | null
    | undefined,
  catalogModels: ModelCatalogItem[],
): NonNullable<ModelCatalogItem["reasoning_effort"]> | null {
  if (!slot) return null;
  const model = slot.model.trim();
  if (!model) return null;
  const item =
    slot.origin != null
      ? findCatalogItem(catalogModels, {
          id: model,
          origin: slot.origin,
          providerId: slot.provider_id,
        })
      : catalogModels.find((m) => m.id === model);
  const spec = item?.reasoning_effort;
  if (!spec?.options.length || !spec.default) return null;
  return spec;
}

/** Effective vendor effort token for a combination (stored if listed, else catalog default). */
export function resolvedProfileEffort(
  profile: {
    main: {
      model: string;
      origin?: ModelCatalogItem["origin"];
      provider_id?: string | null;
    };
    reasoning_effort?: string | null;
  },
  catalogModels: ModelCatalogItem[],
): string | null {
  const spec = catalogReasoningEffort(profile.main, catalogModels);
  if (!spec) return null;
  const stored = profile.reasoning_effort?.trim() ?? "";
  if (stored && spec.options.includes(stored)) return stored;
  return spec.default;
}

/** List the models this user may pick + the account's currently-resolved model. */
export function getModels(): Promise<ModelCatalog> {
  return api.get<ModelCatalog>("/v1/users/me/models");
}

// —— 跨会话的「上次选择」偏好（走统一 UI 持久化层，禁止直碰 localStorage）——

/** 拆分前聊天里记下的装配 id。第一次读装配时抄进 `chat:assembly:last`。 */
const LAST_USED_PROFILE_LEAF = "chat:profile:last";
const LAST_USED_ASSEMBLY_LEAF = "chat:assembly:last";

function readStoredId(leaf: string): string | null {
  const raw = uiGet(leaf);
  if (typeof raw === "string") {
    const id = raw.trim();
    return id || null;
  }
  return null;
}

function writeStoredId(leaf: string, id: string): void {
  const trimmed = id.trim();
  if (!trimmed) return;
  uiSet(leaf, trimmed);
}

/**
 * 上次在聊天里选择的装配。
 * `chat:assembly:last` 还空时，把旧键抄过来一次。
 */
export function getLastUsedAssemblyId(): string | null {
  const current = readStoredId(LAST_USED_ASSEMBLY_LEAF);
  if (current) return current;
  const legacy = readStoredId(LAST_USED_PROFILE_LEAF);
  if (!legacy) return null;
  uiSet(LAST_USED_ASSEMBLY_LEAF, legacy);
  return legacy;
}

export function setLastUsedAssemblyId(assemblyId: string): void {
  writeStoredId(LAST_USED_ASSEMBLY_LEAF, assemblyId);
}

/** 装配上次选择。旧调用点仍用这个名字。 */
export function getLastUsedProfileId(): string | null {
  return getLastUsedAssemblyId();
}

export function setLastUsedProfileId(profileId: string): void {
  setLastUsedAssemblyId(profileId);
}

export function clearLastUsedProfileId(): void {
  uiRemove(LAST_USED_PROFILE_LEAF);
  uiRemove(LAST_USED_ASSEMBLY_LEAF);
}
