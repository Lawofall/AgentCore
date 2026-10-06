import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

/**
 * BYOK 多服务商配置（设置·模型配置）数据层。
 *
 * 一个账号可配置一组 OpenAI 兼容服务商（各自 label + key + 端点）。
 * 星标装配走 `/v1/users/me/assemblies`（见
 * {@link import("@/services/llmModelProfiles")}）。
 * REST 类型由后端 OpenAPI 生成（仓库根 `pnpm gen:types`）。
 */

type Schemas = components["schemas"];

/** 单个 BYOK 服务商的设置视图（永不含明文 Key）。 */
export type LlmProviderView = Schemas["LlmProviderView"];

/**
 * 设置·模型配置的服务商列表 + 部署级能力。
 *
 * 部署级字段（`billing_mode` / `platform_available` / `platform_model` /
 * `default_model_profile_id`）描述账号/部署，不属于任何单个服务商。
 */
export type LlmProvidersResponse = Schemas["LlmProvidersResponse"];

/** 新增服务商入参。 */
export type CreateLlmProviderInput = Schemas["CreateLlmProviderRequest"];
/** 部分更新服务商入参（省略 api_key 保留已存密文）。 */
export type UpdateLlmProviderInput = Schemas["UpdateLlmProviderRequest"];

/** 服务商列表 + 部署能力（设置页 BYOK 区数据源）。 */
export function listLlmProviders(): Promise<LlmProvidersResponse> {
  return api.get<LlmProvidersResponse>("/v1/users/me/llm-providers");
}

/** 新增一个 OpenAI 兼容服务商（Key 加密存储，状态置 'unchecked'）。 */
export function createLlmProvider(
  input: CreateLlmProviderInput,
): Promise<LlmProviderView> {
  return api.post<LlmProviderView>("/v1/users/me/llm-providers", input);
}

/** 更新某服务商（端点 / label；api_key 省略则保留已存 Key）。 */
export function updateLlmProvider(
  providerId: string,
  input: UpdateLlmProviderInput,
): Promise<LlmProviderView> {
  return api.patch<LlmProviderView>(
    `/v1/users/me/llm-providers/${providerId}`,
    input,
  );
}

/** 删除某服务商（组合槽位引用由后端静默回落，不硬失败）。 */
export function deleteLlmProvider(
  providerId: string,
): Promise<{ status: string }> {
  return api.delete<{ status: string }>(
    `/v1/users/me/llm-providers/${providerId}`,
  );
}

/** 探测某服务商端点，持久化并返回 'active' / 'error' + supports_tools。 */
export function testLlmProvider(providerId: string): Promise<LlmProviderView> {
  return api.post<LlmProviderView>(
    `/v1/users/me/llm-providers/${providerId}/test`,
  );
}

/**
 * 账号「有效聊天模型」所在服务商的工具调用支持位——喂给工具软门禁
 * （{@link import("@/lib/llmToolsGate").needsToolsGateHint}）。
 * `providerId` 通常来自 `ModelCatalogResponse.current.provider_id`（默认组合展开后的主槽）；
 * 平台 / keyless 时无 id → undefined（不提示）。
 */
export function defaultChatSupportsTools(
  response: LlmProvidersResponse | undefined | null,
  providerId?: string | null,
): boolean | null | undefined {
  const id = providerId?.trim();
  if (!id) return undefined;
  return response?.providers.find((p) => p.id === id)?.supports_tools;
}
