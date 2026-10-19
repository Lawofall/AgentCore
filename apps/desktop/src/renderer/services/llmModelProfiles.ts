import { api } from "@/services/api";
import {
  type ModelCatalogItem,
  resolvedProfileEffort,
  setLastUsedAssemblyId,
} from "@/services/models";
import type { components } from "@/types/api.generated";

/**
 * 账号「模型组合」CRUD（设置·模型配置 / 输入框组合选择器）。
 *
 * 组合 = `{ main, worker?, reasoning_effort? }`；
 * Worker 空 = 跟随主模型。图走当前主模型。
 * `reasoning_effort` 空 = 主模型厂商默认；仅官方 token。
 * 人选一份时写入 `PUT …/default`（账号回落，人侧不露）；会话只存 `conversations.assembly_id`。
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

/** 删除自建能力。官方完整不能删。 */
export function deleteLlmModelProfile(
  profileId: string,
): Promise<{ status: string }> {
  return api.delete<{ status: string }>(`/v1/users/me/assemblies/${profileId}`);
}

/** 设账号回落装配（系统预置或用户组合均可）。人侧不露。 */
export function setDefaultLlmModelProfile(
  profileId: string,
): Promise<LlmModelProfileView> {
  return api.put<LlmModelProfileView>("/v1/users/me/assemblies/default", {
    profile_id: profileId,
  });
}

/** 人选的这份：本机 last-used + 账号回落。没打开对话时工具/信封写回落。 */
export async function rememberAssemblySelection(
  assemblyId: string,
): Promise<LlmModelProfileView> {
  setLastUsedAssemblyId(assemblyId);
  return setDefaultLlmModelProfile(assemblyId);
}

/** 账号回落装配（`is_default` 或 `default_assembly_id`）。人侧不露。 */
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

/** 槽位展示名：目录 display_name，再回落 model id。 */
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
    ) ??
    catalogModels.find((m) => m.id === slot.model && m.origin !== "platform");
  return match?.display_name?.trim() || slot.model;
}

/**
 * 组合次要摘要：「主 · Worker」与思考强度官方 token。
 * Worker 空 =「跟随主模型」。
 * 主模型方言发 reasoning_effort 时附厂商档（空存储 = 目录默认）。
 */
export function profileSlotSummary(
  profile: {
    main?: ModelProfileSlot | null;
    worker?: ModelProfileSlot | null;
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
  const effort = resolvedProfileEffort(
    { main: slot, reasoning_effort: profile.reasoning_effort },
    catalogModels as ModelCatalogItem[],
  );
  if (effort) parts.push(effort);
  return parts.join(" · ");
}
