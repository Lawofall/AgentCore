import type { ModelProfileSlot } from "@/services/llmModelProfiles";
import type { LlmProviderView } from "@/services/llmProviders";
import type {
  ModelCatalog,
  ModelCatalogItem,
  ModelPriceCard,
} from "@/services/models";
import { findCatalogItem } from "@/services/models";

/**
 * 模型组合槽位选择器的纯逻辑（设置·模型配置 · 编辑组合）。
 *
 * 槽位解析后仍是 `(model, origin, provider_id)`；选择器的值是目录身份
 * `@platform/{id}` / `@byok/{provider_id}/{id}`。按服务商分组呈现 BYOK 候选，
 * 并在平台可用时追加「平台额度」分组；每个服务商的候选 = 其 `default_model` ∪
 * 模型目录里该服务商带出的模型；再把**当前编辑组合**的槽位并入（查目录补显示名），
 * 保证现值始终可选。已删服务商的孤儿槽会单独成组，避免 select 静默错位。
 */

/** 目录项可选 curated 徽章（后端并行加字段；本地窄扩展读取）。 */
export type CatalogItemWithBadge = ModelCatalogItem & {
  badge?: string | null;
};

export type DefaultModelOption = {
  model: string;
  /** 品牌显示名；查不到目录时回落裸 id。 */
  label: string;
  /** curated 展示徽章，如「免费额度」。 */
  badge?: string | null;
  vendor?: string | null;
  contextLength?: number | null;
  capabilities?: string[];
  price?: ModelPriceCard | null;
  /** 不在目录中的手填 / 孤儿 id。 */
  custom?: boolean;
  /** False = listed but not selectable (grey + reason). Default true. */
  available?: boolean;
  unavailableReason?: ModelCatalogItem["unavailable_reason"];
};

/** Client-side copy for a structured catalog unavailability. Null if unknown/absent. */
export function unavailableReasonCopy(
  reason: ModelCatalogItem["unavailable_reason"] | undefined | null,
): string | null {
  if (!reason) return null;
  if (reason.code === "upstream_protocol_unsupported") {
    if (reason.required_protocol === "openai_responses") {
      return "需要 OpenAI /responses 协议，当前接入不支持";
    }
    if (reason.required_protocol === "anthropic_messages") {
      return "需要 Anthropic /messages 协议，当前接入不支持";
    }
    return "当前接入不支持该模型所需协议";
  }
  return null;
}

export type DefaultProviderGroup = {
  providerId: string;
  providerLabel: string;
  models: DefaultModelOption[];
  /** 槽位指向已删除的服务商。 */
  orphan?: boolean;
};

const SEP = "::";
/**
 * Select-value / optgroup id for platform-catalog pointers (not a real provider UUID).
 */
export const PLATFORM_POINTER_ID = "__platform__";

const PLATFORM_REF_PREFIX = "@platform/";
const BYOK_REF_PREFIX = "@byok/";

/** Encode a catalog identity (`@platform/{id}` / `@byok/{provider_id}/{id}`). */
export function formatModelRef(
  origin: "platform" | "byok",
  model: string,
  providerId?: string | null,
): string {
  const mid = model.trim();
  if (!mid) return "";
  if (origin === "platform" || !providerId) {
    return `${PLATFORM_REF_PREFIX}${mid}`;
  }
  return `${BYOK_REF_PREFIX}${providerId}/${mid}`;
}

/** Encode a `(provider_id|__platform__, model)` pair or a full slot into a select value. */
export function encodePointer(
  providerIdOrSlot: string | ModelProfileSlot,
  model?: string,
): string {
  if (typeof providerIdOrSlot === "string") {
    if (isPlatformGroupId(providerIdOrSlot) || !providerIdOrSlot) {
      return formatModelRef("platform", model ?? "");
    }
    return formatModelRef("byok", model ?? "", providerIdOrSlot);
  }
  const p = providerIdOrSlot;
  if (p.origin === "platform" || !p.provider_id) {
    return formatModelRef("platform", p.model);
  }
  return formatModelRef("byok", p.model, p.provider_id);
}

/** The `<select>` value for a slot (empty string = 未设置 / 跟随). */
export function pointerValue(
  slot: ModelProfileSlot | null | undefined,
): string {
  if (!slot?.model) return "";
  return encodePointer(slot);
}

/** Parse a `<select>` option value back into a slot (null for the empty value). */
export function decodePointer(value: string): ModelProfileSlot | null {
  const text = value.trim();
  if (!text) return null;
  const lower = text.toLowerCase();
  if (lower.startsWith(PLATFORM_REF_PREFIX)) {
    const model = text.slice(PLATFORM_REF_PREFIX.length).trim();
    if (!model) return null;
    return { origin: "platform", provider_id: null, model };
  }
  if (lower.startsWith(BYOK_REF_PREFIX)) {
    const rest = text.slice(BYOK_REF_PREFIX.length);
    const slash = rest.indexOf("/");
    if (slash < 0) return null;
    const provider_id = rest.slice(0, slash).trim();
    const model = rest.slice(slash + 1).trim();
    if (!provider_id || !model) return null;
    return { origin: "byok", provider_id, model };
  }
  // Leftover local UI values from the old `__platform__::model` pointer.
  const idx = text.indexOf(SEP);
  if (idx < 0) return null;
  const provider_id = text.slice(0, idx);
  const model = text.slice(idx + SEP.length);
  if (!provider_id || !model) return null;
  if (provider_id === PLATFORM_POINTER_ID) {
    return { origin: "platform", provider_id: null, model };
  }
  return { origin: "byok", provider_id, model };
}

export function isPlatformGroupId(providerId: string): boolean {
  return providerId === PLATFORM_POINTER_ID;
}

/** 读目录可选 `badge`（生成类型未到位时的窄扩展）。 */
export function catalogItemBadge(
  item: ModelCatalogItem | CatalogItemWithBadge | undefined,
): string | null {
  if (!item) return null;
  const badge = (item as CatalogItemWithBadge).badge;
  return typeof badge === "string" && badge.trim() ? badge.trim() : null;
}

function optionFromCatalogItem(item: ModelCatalogItem): DefaultModelOption {
  return {
    model: item.id.trim(),
    label: item.display_name?.trim() || item.id.trim(),
    badge: catalogItemBadge(item),
    vendor: item.vendor?.trim() || null,
    contextLength: item.context_length ?? null,
    capabilities: item.capabilities ?? [],
    price: item.price ?? null,
    custom: false,
    available: item.available !== false,
    unavailableReason: item.unavailable_reason ?? null,
  };
}

function resolveSlotOption(
  slot: ModelProfileSlot,
  catalog: ModelCatalog | undefined,
): DefaultModelOption {
  const item = findCatalogItem(catalog?.models ?? [], {
    id: slot.model,
    origin: slot.origin,
    providerId: slot.provider_id,
  });
  if (item) {
    return optionFromCatalogItem(item);
  }
  return {
    model: slot.model,
    label: slot.model,
    custom: true,
  };
}

/**
 * Build the per-provider option groups for slot selectors.
 * Includes a 「平台额度」 group when the catalog exposes `origin=platform` rows
 * (unavailable rows stay listed so the picker can grey them and show why).
 * `slots` are **current-edit** pointers only — folded in with catalog labels when possible.
 */
export function buildDefaultProviderGroups(
  providers: LlmProviderView[],
  catalog: ModelCatalog | undefined,
  ...slots: (ModelProfileSlot | null | undefined)[]
): DefaultProviderGroup[] {
  const groups: DefaultProviderGroup[] = providers.map((p) => {
    const models: DefaultModelOption[] = [];
    const seen = new Set<string>();
    const add = (opt: DefaultModelOption) => {
      const m = opt.model.trim();
      if (!m || seen.has(m)) return;
      seen.add(m);
      models.push({ ...opt, model: m });
    };
    for (const item of catalog?.models ?? []) {
      if (item.origin === "byok" && item.provider_id === p.id) {
        add(optionFromCatalogItem(item));
      }
    }
    if (p.default_model) {
      const dm = p.default_model.trim();
      if (dm && !seen.has(dm)) {
        const item = findCatalogItem(catalog?.models ?? [], {
          id: dm,
          origin: "byok",
          providerId: p.id,
        });
        add(
          item
            ? optionFromCatalogItem(item)
            : { model: dm, label: dm, custom: false },
        );
      }
    }
    return {
      providerId: p.id,
      providerLabel: p.label?.trim() || p.base_url,
      models,
    };
  });

  const platformModels: DefaultModelOption[] = [];
  const platformSeen = new Set<string>();
  for (const item of catalog?.models ?? []) {
    if (item.origin !== "platform") continue;
    const m = item.id.trim();
    if (!m || platformSeen.has(m)) continue;
    platformSeen.add(m);
    platformModels.push(optionFromCatalogItem(item));
  }
  if (platformModels.length > 0) {
    groups.unshift({
      providerId: PLATFORM_POINTER_ID,
      providerLabel: "平台额度",
      models: platformModels,
    });
  }

  const knownProviderIds = new Set(providers.map((p) => p.id));

  for (const slot of slots) {
    if (!slot?.model) continue;
    const groupId =
      slot.origin === "platform" || !slot.provider_id
        ? PLATFORM_POINTER_ID
        : slot.provider_id;
    let group = groups.find((g) => g.providerId === groupId);
    if (!group) {
      if (groupId === PLATFORM_POINTER_ID) {
        group = {
          providerId: PLATFORM_POINTER_ID,
          providerLabel: "平台额度",
          models: [],
        };
        groups.unshift(group);
      } else if (!knownProviderIds.has(groupId)) {
        group = {
          providerId: groupId,
          providerLabel: "已移除的服务商",
          models: [],
          orphan: true,
        };
        // 孤儿组紧跟平台组之后，便于用户看见并改选。
        const platformIdx = groups.findIndex(
          (g) => g.providerId === PLATFORM_POINTER_ID,
        );
        groups.splice(platformIdx + 1, 0, group);
      }
    }
    if (group && !group.models.some((m) => m.model === slot.model)) {
      group.models.unshift(resolveSlotOption(slot, catalog));
    }
  }
  return groups;
}

/** 当前 model 是否属于该渠道目录（不含 custom 折叠项）。 */
export function modelInChannelCatalog(
  group: DefaultProviderGroup | undefined,
  model: string,
): boolean {
  if (!group || !model.trim()) return false;
  const hit = group.models.find((m) => m.model === model.trim());
  return Boolean(hit && !hit.custom);
}
