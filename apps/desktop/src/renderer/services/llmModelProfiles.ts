import { api } from "@/services/api";
import {
  type ModelCatalogItem,
  resolvedProfileEffort,
} from "@/services/models";
import type { components } from "@/types/api.generated";

/**
 * 账号「模型组合」CRUD（设置·模型配置 / 输入框组合选择器）。
 *
 * 组合 = `{ main, worker?, background?, vision?, reasoning_effort? }`；
 * Worker / 后台空 = 跟随主模型；vision 槽 API 仍在，产品不再用（图走当前主模型）。
 * `reasoning_effort` 空 = 主模型厂商默认；仅官方 token。
 * 星标写在 `PUT …/default`；会话只存 `conversations.assembly_id`。
 */

type Schemas = components["schemas"];

export type ModelProfileSlot = Schemas["ModelProfileSlot"];
export type LlmModelProfileView = Schemas["LlmModelProfileView"];
export type LlmModelProfileListResponse =
  Schemas["LlmModelProfileListResponse"];
export type CreateLlmModelProfileInput =
  Schemas["CreateLlmModelProfileRequest"];
export type UpdateLlmModelProfileInput =
  Schemas["UpdateLlmModelProfileRequest"];

/** 列出账号可用组合（系统预置 + 用户 + 隐式）与默认组合 id。 */
export function listLlmModelProfiles(): Promise<LlmModelProfileListResponse> {
  return api.get<LlmModelProfileListResponse>("/v1/users/me/assemblies");
}

/** 新建用户组合。 */
export function createLlmModelProfile(
  input: CreateLlmModelProfileInput,
): Promise<LlmModelProfileView> {
  return api.post<LlmModelProfileView>("/v1/users/me/assemblies", input);
}

/** 部分更新用户组合（系统预置不可改槽位时由后端 422）。 */
export function updateLlmModelProfile(
  profileId: string,
  input: UpdateLlmModelProfileInput,
): Promise<LlmModelProfileView> {
  return api.patch<LlmModelProfileView>(
    `/v1/users/me/assemblies/${profileId}`,
    input,
  );
}

/** 删除用户组合（系统预置不可删）。 */
export function deleteLlmModelProfile(
  profileId: string,
): Promise<{ status: string }> {
  return api.delete<{ status: string }>(`/v1/users/me/assemblies/${profileId}`);
}

/** 设账号默认组合（系统预置或用户组合均可）。 */
export function setDefaultLlmModelProfile(
  profileId: string,
): Promise<LlmModelProfileView> {
  return api.put<LlmModelProfileView>("/v1/users/me/assemblies/default", {
    profile_id: profileId,
  });
}

/** 列表中的星标装配（`is_default` 或 `default_assembly_id`）。 */
export function resolveDefaultProfile(
  response: LlmModelProfileListResponse | undefined | null,
): LlmModelProfileView | undefined {
  if (!response) return undefined;
  const id = response.default_assembly_id;
  if (id) {
    const hit = response.data.find((p) => p.id === id);
    if (hit) return hit;
  }
  return response.data.find((p) => p.is_default) ?? response.data[0];
}

/** 槽位展示名：目录 display_name 优先，否则 model id。 */
export function slotDisplayName(
  slot: ModelProfileSlot | null | undefined,
  catalogModels: {
    id: string;
    origin: string;
    display_name: string;
    provider_id?: string | null;
  }[],
): string {
  if (!slot?.model) return "";
  const match =
    catalogModels.find(
      (m) =>
        m.id === slot.model &&
        m.origin === slot.origin &&
        (slot.origin !== "byok" ||
          !slot.provider_id ||
          m.provider_id === slot.provider_id),
    ) ?? catalogModels.find((m) => m.id === slot.model);
  return match?.display_name?.trim() || slot.model;
}

/**
 * 组合次要摘要：「主 · Worker」，有覆盖时再附「后台」与思考强度官方 token。
 * Worker 空 =「跟随主模型」；后台仅在已配置时追加（列表行勿撑宽）。
 * 主模型方言发 reasoning_effort 时附厂商档（空存储 = 目录默认）。
 */
export function profileSlotSummary(
  profile: {
    main?: ModelProfileSlot | null;
    worker?: ModelProfileSlot | null;
    background?: ModelProfileSlot | null;
    reasoning_effort?: string | null;
  },
  catalogModels: {
    id: string;
    origin: string;
    display_name: string;
    provider_id?: string | null;
    reasoning_effort?: ModelCatalogItem["reasoning_effort"];
  }[],
): string {
  const slot = profile.main;
  if (!slot?.model) return "未设主模型";
  const main = slotDisplayName(slot, catalogModels) || slot.model;
  const worker = profile.worker
    ? slotDisplayName(profile.worker, catalogModels) || profile.worker.model
    : "跟随主模型";
  const parts = [`${main} · ${worker}`];
  if (profile.background?.model) {
    const bg =
      slotDisplayName(profile.background, catalogModels) ||
      profile.background.model;
    parts.push(`后台 ${bg}`);
  }
  const effort = resolvedProfileEffort(
    { main: slot, reasoning_effort: profile.reasoning_effort },
    catalogModels as ModelCatalogItem[],
  );
  if (effort) parts.push(effort);
  return parts.join(" · ");
}
