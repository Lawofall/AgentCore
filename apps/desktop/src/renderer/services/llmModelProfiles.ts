import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

/**
 * 账号「模型组合」CRUD（设置·模型配置 / 输入框组合选择器）。
 *
 * 组合 = `{ main, worker?, background?, vision? }`；
 * Worker / 后台空 = 跟随主模型；vision 空 = 组合未配专用识图槽（解析时 main 收图可复用 main，否则 platform VISION_* 或无 reader）。
 * 账号默认写在 `PUT …/default`；会话引用走 `conversations.model_profile_id`。
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
  return api.get<LlmModelProfileListResponse>(
    "/v1/users/me/llm-model-profiles",
  );
}

/** 新建用户组合。 */
export function createLlmModelProfile(
  input: CreateLlmModelProfileInput,
): Promise<LlmModelProfileView> {
  return api.post<LlmModelProfileView>(
    "/v1/users/me/llm-model-profiles",
    input,
  );
}

/** 部分更新用户组合（系统预置不可改槽位时由后端 422）。 */
export function updateLlmModelProfile(
  profileId: string,
  input: UpdateLlmModelProfileInput,
): Promise<LlmModelProfileView> {
  return api.patch<LlmModelProfileView>(
    `/v1/users/me/llm-model-profiles/${profileId}`,
    input,
  );
}

/** 删除用户组合（系统预置不可删）。 */
export function deleteLlmModelProfile(
  profileId: string,
): Promise<{ status: string }> {
  return api.delete<{ status: string }>(
    `/v1/users/me/llm-model-profiles/${profileId}`,
  );
}

/** 设账号默认组合（系统预置或用户组合均可）。 */
export function setDefaultLlmModelProfile(
  profileId: string,
): Promise<LlmModelProfileView> {
  return api.put<LlmModelProfileView>(
    "/v1/users/me/llm-model-profiles/default",
    { profile_id: profileId },
  );
}

/** 列表中的账号默认组合（`is_default` 或 `default_model_profile_id`）。 */
export function resolveDefaultProfile(
  response: LlmModelProfileListResponse | undefined | null,
): LlmModelProfileView | undefined {
  if (!response) return undefined;
  const id = response.default_model_profile_id;
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
 * 组合次要摘要：「主 · Worker」，有覆盖时再附「后台 / 识图」。
 * Worker 空 =「跟随主模型」；后台 / 识图仅在已配置时追加（列表行勿撑宽）。
 */
export function profileSlotSummary(
  profile: LlmModelProfileView,
  catalogModels: {
    id: string;
    origin: string;
    display_name: string;
    provider_id?: string | null;
  }[],
): string {
  const main =
    slotDisplayName(profile.main, catalogModels) || profile.main.model;
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
  if (profile.vision?.model) {
    const vision =
      slotDisplayName(profile.vision, catalogModels) || profile.vision.model;
    parts.push(`识图 ${vision}`);
  }
  return parts.join(" · ");
}
