// @vitest-environment jsdom
/**
 * Tests for 设置·模型 (model combinations / account default profile).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useLlmProviders", () => ({ useLlmProviders: vi.fn() }));
vi.mock("@/hooks/useLlmModelProfiles", () => ({
  useLlmModelProfiles: vi.fn(),
}));
vi.mock("@/hooks/useModels", () => ({ useModels: vi.fn() }));
vi.mock("@/services/llmModelProfiles", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/llmModelProfiles")>()),
  createLlmModelProfile: vi.fn(),
  updateLlmModelProfile: vi.fn(),
  deleteLlmModelProfile: vi.fn(() => Promise.resolve({ status: "ok" })),
  setDefaultLlmModelProfile: vi.fn(),
}));

import { TooltipProvider } from "@/components/ui/tooltip";
import { useLlmModelProfiles } from "@/hooks/useLlmModelProfiles";
import { useLlmProviders } from "@/hooks/useLlmProviders";
import { useModels } from "@/hooks/useModels";
import { ApiError } from "@/services/api";
import type { LlmModelProfileListResponse } from "@/services/llmModelProfiles";
import {
  createLlmModelProfile,
  deleteLlmModelProfile,
  setDefaultLlmModelProfile,
  updateLlmModelProfile,
} from "@/services/llmModelProfiles";
import type { LlmProvidersResponse } from "@/services/llmProviders";
import { ModelSettings } from "../ModelSettings";

const useLlmProvidersMock = vi.mocked(useLlmProviders);
const useProfilesMock = vi.mocked(useLlmModelProfiles);
const useModelsMock = vi.mocked(useModels);

function providersResponse(
  over: Partial<LlmProvidersResponse> = {},
): LlmProvidersResponse {
  return {
    providers: [
      {
        id: "p1",
        label: "DeepSeek",
        base_url: "https://api.deepseek.com/v1",
        default_model: "deepseek-v4-pro",
        status: "active",
        masked_key: "••••abcd",
        supports_tools: true,
      },
      {
        id: "p2",
        label: "OpenAI",
        base_url: "https://api.openai.com/v1",
        default_model: "gpt-4o",
        status: "unchecked",
        masked_key: "••••wxyz",
      },
    ],
    default_model_profile_id: "sys-52",
    billing_mode: "byok",
    platform_available: false,
    platform_model: null,
    ...over,
  };
}

function profilesResponse(
  over: Partial<LlmModelProfileListResponse> = {},
): LlmModelProfileListResponse {
  return {
    default_model_profile_id: "sys-52",
    data: [
      {
        id: "sys-52",
        name: "GLM-5.2",
        kind: "system",
        is_default: true,
        main: { origin: "byok", provider_id: "p1", model: "deepseek-v4-pro" },
        worker: null,
        background: null,
      },
      {
        id: "user-mine",
        name: "办公",
        kind: "user",
        is_default: false,
        main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
        worker: null,
        background: null,
      },
    ],
    ...over,
  };
}

function mockProviders(data: LlmProvidersResponse | undefined): void {
  useLlmProvidersMock.mockReturnValue({
    data,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useLlmProviders>);
}

function mockProfiles(data: LlmModelProfileListResponse | undefined): void {
  useProfilesMock.mockReturnValue({
    data,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useLlmModelProfiles>);
}

function defaultCatalog() {
  return {
    byok_configured: true,
    current: { id: "deepseek-v4-pro", origin: "byok", provider_id: "p1" },
    models: [
      {
        id: "deepseek-v4-pro",
        origin: "byok" as const,
        display_name: "DeepSeek V4 Pro",
        vendor: "DeepSeek",
        provider_id: "p1",
        provider_label: "DeepSeek",
        capabilities: [] as string[],
        available: true,
      },
      {
        id: "gpt-4o",
        origin: "byok" as const,
        display_name: "GPT-4o",
        vendor: "OpenAI",
        provider_id: "p2",
        provider_label: "OpenAI",
        capabilities: [] as string[],
        available: true,
      },
    ],
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <TooltipProvider>
          <ModelSettings />
        </TooltipProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** Open the rich model list for a slot trigger (id = profile-main | worker | …). */
function openModelPicker(slotId: string) {
  const trigger = document.getElementById(slotId);
  expect(trigger).toBeTruthy();
  if (!trigger) throw new Error(`expected trigger #${slotId}`);
  fireEvent.click(trigger);
  return screen.getByRole("listbox");
}

/** Click「自定义 model id…」under a provider group (data-provider-group). */
function enterCustomModelId(
  slotId: string,
  modelId: string,
  providerId?: string,
) {
  const list = openModelPicker(slotId);
  if (providerId) {
    const section = list.querySelector(`[data-provider-group="${providerId}"]`);
    expect(section).toBeTruthy();
    fireEvent.click(
      within(section as HTMLElement).getByRole("option", {
        name: /自定义 model id/,
      }),
    );
  } else {
    fireEvent.click(
      within(list).getAllByRole("option", { name: /自定义 model id/ })[0],
    );
  }
  const input = screen.getByLabelText("自定义 model id") as HTMLInputElement;
  fireEvent.change(input, { target: { value: modelId } });
  return input;
}

beforeEach(() => {
  useModelsMock.mockReturnValue({
    data: defaultCatalog(),
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useModels>);
  mockProfiles(profilesResponse());
  vi.mocked(deleteLlmModelProfile).mockClear();
  vi.mocked(setDefaultLlmModelProfile).mockClear();
  vi.mocked(createLlmModelProfile).mockClear();
  vi.mocked(updateLlmModelProfile).mockClear();
});

afterEach(cleanup);

describe("ModelSettings (profiles)", () => {
  it("renders model combinations without provider key cards", () => {
    mockProviders(providersResponse());
    renderPage();
    expect(screen.getByText("模型组合")).toBeTruthy();
    expect(screen.getByText(/主模型必填，其余槽位可留空/)).toBeTruthy();
    expect(screen.queryByText(/多人协作（委派）对工具调用要求较高/)).toBeNull();
    expect(screen.getByText("GLM-5.2")).toBeTruthy();
    expect(screen.getByText("办公")).toBeTruthy();
    expect(screen.getByText("默认组合")).toBeTruthy();
    expect(screen.queryByText("••••abcd")).toBeNull();
    expect(screen.queryByRole("button", { name: "添加服务商" })).toBeNull();
  });

  it("does not show a platform status line", () => {
    mockProviders(
      providersResponse({
        providers: [],
        platform_available: true,
        platform_model: "deepseek-v4-flash",
        billing_mode: "platform",
      }),
    );
    mockProfiles(
      profilesResponse({
        data: [
          {
            id: "sys-52",
            name: "GLM-5.2",
            kind: "system",
            is_default: true,
            main: {
              origin: "platform",
              provider_id: null,
              model: "deepseek-v4-flash",
            },
            worker: null,
            background: null,
          },
        ],
      }),
    );
    renderPage();
    expect(screen.queryByText(/可用平台额度 · deepseek-v4-flash/)).toBeNull();
    expect(screen.queryByRole("link", { name: "接入服务商" })).toBeNull();
    expect(screen.queryByRole("link", { name: "管理服务商" })).toBeNull();
    expect(screen.queryByText(/平台免费额度/)).toBeNull();
  });

  it("sets a user profile as the account default", async () => {
    vi.mocked(setDefaultLlmModelProfile).mockResolvedValue({
      id: "user-mine",
      name: "办公",
      kind: "user",
      is_default: true,
      main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
    });
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "设为默认" }));
    await waitFor(() =>
      expect(setDefaultLlmModelProfile).toHaveBeenCalledWith("user-mine"),
    );
    expect(screen.getByText(/已将「办公」设为默认组合/)).toBeTruthy();
  });

  it("copies a system preset into a user profile", async () => {
    vi.mocked(createLlmModelProfile).mockResolvedValue({
      id: "user-copy",
      name: "GLM-5.2 副本",
      kind: "user",
      is_default: false,
      main: { origin: "byok", provider_id: "p1", model: "deepseek-v4-pro" },
    });
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getAllByRole("button", { name: "复制" })[0]);
    await waitFor(() =>
      expect(createLlmModelProfile).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "GLM-5.2 副本",
          main: {
            origin: "byok",
            provider_id: "p1",
            model: "deepseek-v4-pro",
          },
          set_as_default: false,
        }),
      ),
    );
  });

  it("deletes a user profile after confirming in the dialog", async () => {
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/删除组合/)).toBeTruthy();
    expect(deleteLlmModelProfile).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "删除" }));
    await waitFor(() =>
      expect(deleteLlmModelProfile).toHaveBeenCalledWith("user-mine"),
    );
  });

  it("does not offer delete on system presets", () => {
    mockProviders(providersResponse());
    renderPage();
    expect(screen.getByText("GLM-5.2")).toBeTruthy();
    expect(screen.getByText("预置")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "删除" })).toHaveLength(1);
  });

  it("opens the create editor with optional slots collapsed under 高级", () => {
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    expect(screen.getByText("新建组合", { selector: "p" })).toBeTruthy();
    expect(screen.getByText("主模型")).toBeTruthy();
    expect(screen.getByText("必填")).toBeTruthy();
    expect(screen.getByText("高级 · 其他模型")).toBeTruthy();
    expect(
      screen.getByText("组队/后台：跟随主模型 · 识图：不配置"),
    ).toBeTruthy();
    expect(screen.queryByText("组队队员")).toBeNull();
    expect(screen.queryByText("后台任务")).toBeNull();
    expect(screen.queryByText("识图模型（可选）")).toBeNull();
    expect(screen.queryByText(/分槽覆盖/)).toBeNull();
    expect(screen.queryByText("Worker 模型")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /高级 · 其他模型/ }));
    expect(screen.getByText("组队队员")).toBeTruthy();
    expect(screen.getByText("后台任务")).toBeTruthy();
    expect(screen.getByText("识图模型（可选）")).toBeTruthy();
    // 空态只出现在触发器，下方不再重复裸文案。
    expect(screen.getAllByText("跟随主模型")).toHaveLength(2);
    expect(screen.getAllByText("不配置")).toHaveLength(1);
    expect(screen.getByText(/辩论仍用主模型/)).toBeTruthy();
    expect(screen.getByText(/标题、记忆等/)).toBeTruthy();
    expect(screen.getByText(/主模型不能看图时再配/)).toBeTruthy();
  });

  it("hints when draft main is catalog vision-capable", () => {
    useModelsMock.mockReturnValue({
      data: {
        ...defaultCatalog(),
        current: { id: "gpt-4o", origin: "byok", provider_id: "p2" },
        models: [
          defaultCatalog().models[0],
          {
            ...defaultCatalog().models[1],
            capabilities: ["vision"],
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useModels>);
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    const list = openModelPicker("profile-main");
    fireEvent.click(within(list).getByRole("option", { name: /GPT-4o/ }));
    fireEvent.click(screen.getByRole("button", { name: /高级 · 其他模型/ }));
    expect(
      screen.getByText(/主模型已可看图，本槽供白板等按需深读/),
    ).toBeTruthy();
    // 条件提示合并进同一句，不再额外多出一行。
    expect(screen.queryByText(/主模型不能看图时再配/)).toBeNull();
  });

  it("greys off-protocol catalog rows and shows why they cannot be selected", () => {
    useModelsMock.mockReturnValue({
      data: {
        ...defaultCatalog(),
        models: [
          ...defaultCatalog().models,
          {
            id: "grok-4.5",
            origin: "byok" as const,
            display_name: "Grok 4.5",
            vendor: "xAI",
            provider_id: "p1",
            provider_label: "DeepSeek",
            capabilities: [] as string[],
            available: false,
            unavailable_reason: {
              code: "upstream_protocol_unsupported",
              required_protocol: "openai_responses",
            },
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useModels>);
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    const list = openModelPicker("profile-main");
    const grok = within(list).getByRole("option", { name: /Grok 4.5/ });
    expect(grok.getAttribute("aria-disabled")).toBe("true");
    expect(grok.textContent).toMatch(/\/responses/);
    fireEvent.click(grok);
    expect(document.getElementById("profile-main")?.textContent).not.toMatch(
      /Grok 4.5/,
    );
  });

  it("saves an edited user profile and shows success feedback", async () => {
    vi.mocked(updateLlmModelProfile).mockResolvedValue({
      id: "user-mine",
      name: "办公",
      kind: "user",
      is_default: false,
      main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
      worker: null,
      background: null,
    });
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(screen.getByText("编辑组合", { selector: "p" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(updateLlmModelProfile).toHaveBeenCalledWith(
        "user-mine",
        expect.objectContaining({
          name: "办公",
          main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
        }),
      ),
    );
    await waitFor(() =>
      expect(screen.queryByText("编辑组合", { selector: "p" })).toBeNull(),
    );
    expect(screen.getByText(/「办公」已保存/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "编辑" })).toBeTruthy();
    expect(screen.queryByText("已保存，但请留意模型可达性")).toBeNull();
    expect(screen.queryByText("模型提醒")).toBeNull();
  });

  it("shows save warnings as non-failure reminders after a successful save", async () => {
    const warning = "主模型 模型「gpt5.6」可能不可用：上游目录未列出该模型";
    vi.mocked(updateLlmModelProfile).mockResolvedValue({
      id: "user-mine",
      name: "办公",
      kind: "user",
      is_default: false,
      main: { origin: "byok", provider_id: "p2", model: "gpt5.6" },
      worker: null,
      background: null,
      warnings: [warning],
    });
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(screen.getByText(/「办公」已保存/)).toBeTruthy(),
    );
    expect(screen.queryByText("编辑组合", { selector: "p" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    const statuses = screen.getAllByRole("status");
    expect(statuses.some((el) => el.textContent?.includes(warning))).toBe(true);
    expect(screen.getByText("已保存，但请留意模型可达性")).toBeTruthy();
    expect(screen.getByText("模型提醒")).toBeTruthy();
    expect(screen.getByText(warning)).toBeTruthy();
    // 成功路径：绿色成功文案仍在，警告不是 destructive。
    expect(screen.getByText(/「办公」已保存/).className).toContain(
      "text-success",
    );
    const warningBox = statuses.find((el) => el.textContent?.includes(warning));
    expect(warningBox?.className).toContain("text-warning");
    expect(warningBox?.className).not.toContain("text-destructive");
  });

  it("does not show save-warning UI when the save response has no warnings", async () => {
    vi.mocked(updateLlmModelProfile).mockResolvedValue({
      id: "user-mine",
      name: "办公",
      kind: "user",
      is_default: false,
      main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
      worker: null,
      background: null,
      warnings: [],
    });
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(screen.getByText(/「办公」已保存/)).toBeTruthy(),
    );
    expect(screen.queryByText("已保存，但请留意模型可达性")).toBeNull();
    expect(screen.queryByText("模型提醒")).toBeNull();
  });

  it("shows save errors inline in the editor card", async () => {
    vi.mocked(updateLlmModelProfile).mockRejectedValue(
      new ApiError(422, JSON.stringify({ detail: "invalid model" })),
    );
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText("编辑组合", { selector: "p" })).toBeTruthy();
  });

  it("shows combinations for keyless platform users", () => {
    useModelsMock.mockReturnValue({
      data: {
        byok_configured: false,
        current: { id: "platform-flash", origin: "platform" },
        models: [
          {
            id: "platform-flash",
            origin: "platform",
            display_name: "Flash (平台)",
            vendor: "Platform",
            capabilities: [],
            available: true,
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useModels>);
    mockProviders(
      providersResponse({
        providers: [],
        platform_available: true,
        platform_model: "platform-flash",
        billing_mode: "platform",
      }),
    );
    mockProfiles(
      profilesResponse({
        data: [
          {
            id: "sys-52",
            name: "GLM-5.2",
            kind: "system",
            is_default: true,
            main: {
              origin: "platform",
              provider_id: null,
              model: "platform-flash",
            },
            worker: null,
            background: null,
          },
        ],
      }),
    );
    renderPage();
    expect(screen.getByText("模型组合")).toBeTruthy();
    expect(screen.getByText("GLM-5.2")).toBeTruthy();
  });

  it("shows empty CTA to providers when byok has no providers or platform", () => {
    mockProviders(
      providersResponse({
        providers: [],
        platform_available: false,
        billing_mode: "byok",
      }),
    );
    renderPage();
    expect(screen.getByRole("link", { name: "接入服务商" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "接入服务商" })).toBeTruthy();
    expect(screen.getByText(/需自行接入服务商后才能对话/)).toBeTruthy();
    expect(screen.queryByText("模型组合")).toBeNull();
  });

  it("on 新建 with BYOK but empty catalog opens editor with custom entry", () => {
    useModelsMock.mockReturnValue({
      data: {
        byok_configured: true,
        current: undefined,
        models: [],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useModels>);
    mockProviders(
      providersResponse({
        providers: [
          {
            id: "p1",
            label: "DeepSeek",
            base_url: "https://api.deepseek.com/v1",
            default_model: "",
            status: "active",
            masked_key: "••••abcd",
            supports_tools: true,
          },
        ],
        platform_available: false,
      }),
    );
    mockProfiles(profilesResponse({ data: [] }));
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    expect(screen.getByText("新建组合", { selector: "p" })).toBeTruthy();
    openModelPicker("profile-main");
    expect(
      screen.getByRole("option", { name: /自定义 model id/ }),
    ).toBeTruthy();
    expect(screen.queryByText(/暂无可用模型/)).toBeNull();
    expect(document.querySelector("datalist")).toBeNull();
  });

  it("on 新建 when seedMain fails with platform_available shows retry/settings guide", () => {
    useModelsMock.mockReturnValue({
      data: {
        byok_configured: false,
        current: undefined,
        models: [],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useModels>);
    mockProviders(
      providersResponse({
        providers: [],
        platform_available: true,
        billing_mode: "platform",
      }),
    );
    mockProfiles(profilesResponse({ data: [] }));
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    expect(screen.queryByText("新建组合", { selector: "p" })).toBeNull();
    expect(screen.getByText(/请稍后重试/)).toBeTruthy();
    expect(
      screen.getAllByRole("link", { name: "设置 · 服务商" }).length,
    ).toBeGreaterThan(0);
  });

  it("when groups have no catalog models but BYOK exists, custom entry stays available", () => {
    useModelsMock.mockReturnValue({
      data: {
        byok_configured: true,
        current: undefined,
        models: [],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useModels>);
    mockProviders(
      providersResponse({
        providers: [
          {
            id: "p1",
            label: "DeepSeek",
            base_url: "https://api.deepseek.com/v1",
            default_model: "",
            status: "active",
            masked_key: "••••abcd",
            supports_tools: true,
          },
        ],
        platform_available: false,
      }),
    );
    mockProfiles(profilesResponse({ data: [] }));
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    expect(screen.getByText("新建组合", { selector: "p" })).toBeTruthy();
    expect(screen.queryByText(/暂无可用模型/)).toBeNull();
    openModelPicker("profile-main");
    expect(
      screen.getAllByRole("option", { name: /自定义 model id/ }).length,
    ).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /高级 · 其他模型/ }));
    expect(document.getElementById("profile-worker")).toBeTruthy();
    expect(document.getElementById("profile-background")).toBeTruthy();
    expect(document.getElementById("profile-vision")).toBeTruthy();
  });

  it("when groups have no models with platform_available, editor shows retry/settings guide", () => {
    useModelsMock.mockReturnValue({
      data: {
        byok_configured: false,
        current: {
          id: "orphan-model",
          origin: "platform",
        },
        models: [],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useModels>);
    mockProviders(
      providersResponse({
        providers: [],
        platform_available: true,
        platform_model: "orphan-model",
        billing_mode: "platform",
      }),
    );
    mockProfiles(profilesResponse({ data: [] }));
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    expect(screen.getByText("新建组合", { selector: "p" })).toBeTruthy();
    expect(screen.getByText(/请稍后重试/)).toBeTruthy();
    expect(
      screen.getAllByRole("link", { name: "设置 · 服务商" }).length,
    ).toBeGreaterThan(0);
    expect(document.getElementById("profile-main")).toBeTruthy();
    expect(document.getElementById("profile-main-provider")).toBeNull();
    expect(screen.queryByText(/自定义 model id/)).toBeNull();
  });

  it("custom entry can fill ep- and marks as custom", async () => {
    vi.mocked(updateLlmModelProfile).mockResolvedValue({
      id: "user-mine",
      name: "办公",
      kind: "user",
      is_default: false,
      main: { origin: "byok", provider_id: "p1", model: "ep-volc-123" },
      worker: null,
      background: null,
    });
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));

    enterCustomModelId("profile-main", "ep-volc-123", "p1");
    expect(screen.getByText("自定义")).toBeTruthy();
    expect(screen.getByText("DeepSeek")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(updateLlmModelProfile).toHaveBeenCalledWith(
        "user-mine",
        expect.objectContaining({
          main: {
            origin: "byok",
            provider_id: "p1",
            model: "ep-volc-123",
          },
        }),
      ),
    );
  });

  it("echoes a saved custom BYOK model id in custom mode", () => {
    mockProviders(providersResponse());
    mockProfiles(
      profilesResponse({
        data: [
          {
            id: "user-mine",
            name: "办公",
            kind: "user",
            is_default: false,
            main: {
              origin: "byok",
              provider_id: "p1",
              model: "ep-already-saved",
            },
            worker: null,
            background: null,
          },
        ],
      }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(document.getElementById("profile-main-provider")).toBeNull();
    const input = screen.getByLabelText("自定义 model id") as HTMLInputElement;
    expect(input.value).toBe("ep-already-saved");
    expect(screen.getByText("自定义")).toBeTruthy();
    expect(screen.getByText("DeepSeek")).toBeTruthy();
    expect(document.querySelector("datalist")).toBeNull();
  });

  it("platform-only catalog has no custom entry", () => {
    useModelsMock.mockReturnValue({
      data: {
        byok_configured: false,
        current: { id: "platform-flash", origin: "platform" },
        models: [
          {
            id: "platform-flash",
            origin: "platform",
            display_name: "Flash (平台)",
            vendor: "Platform",
            capabilities: [],
            available: true,
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useModels>);
    mockProviders(
      providersResponse({
        providers: [],
        platform_available: true,
        platform_model: "platform-flash",
        billing_mode: "platform",
      }),
    );
    mockProfiles(
      profilesResponse({
        data: [
          {
            id: "user-plat",
            name: "平台组合",
            kind: "user",
            is_default: true,
            main: {
              origin: "platform",
              provider_id: null,
              model: "platform-flash",
            },
            worker: null,
            background: null,
          },
        ],
      }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(document.getElementById("profile-main-provider")).toBeNull();
    openModelPicker("profile-main");
    expect(
      screen.queryByRole("option", { name: /自定义 model id/ }),
    ).toBeNull();
    expect(screen.queryByPlaceholderText(/model id/)).toBeNull();
  });

  it("keeps platform SKUs under the platform group, not a BYOK channel", () => {
    useModelsMock.mockReturnValue({
      data: {
        byok_configured: true,
        current: { id: "deepseek-v4-flash", origin: "byok", provider_id: "p1" },
        models: [
          {
            id: "deepseek-v4-flash",
            origin: "byok",
            display_name: "DeepSeek V4 Flash",
            vendor: "DeepSeek",
            provider_id: "p1",
            provider_label: "DeepSeek",
            capabilities: [],
            available: true,
          },
          {
            id: "deepseek-v4-flash-free",
            origin: "platform",
            display_name: "Flash Free",
            vendor: "OpenCode Zen",
            provider_id: null,
            capabilities: [],
            available: true,
            badge: "免费额度",
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useModels>);
    mockProviders(
      providersResponse({
        providers: [
          {
            id: "p1",
            label: "DeepSeek",
            base_url: "https://api.deepseek.com/v1",
            default_model: "deepseek-v4-flash",
            status: "active",
            masked_key: "••••abcd",
          },
        ],
        platform_available: true,
        platform_model: "deepseek-v4-flash-free",
      }),
    );
    mockProfiles(
      profilesResponse({
        data: [
          {
            id: "user-a",
            name: "DeepSeek 组合",
            kind: "user",
            is_default: true,
            main: {
              origin: "byok",
              provider_id: "p1",
              model: "deepseek-v4-flash",
            },
            worker: null,
            background: null,
          },
          {
            id: "user-b",
            name: "平台组合",
            kind: "user",
            is_default: false,
            main: {
              origin: "platform",
              provider_id: null,
              model: "deepseek-v4-flash-free",
            },
            worker: null,
            background: null,
          },
        ],
      }),
    );
    renderPage();
    fireEvent.click(screen.getAllByRole("button", { name: "编辑" })[0]);
    const list = openModelPicker("profile-main");
    const deepseek = list.querySelector('[data-provider-group="p1"]');
    expect(deepseek).toBeTruthy();
    expect(
      within(deepseek as HTMLElement).getByText("DeepSeek V4 Flash"),
    ).toBeTruthy();
    expect(
      within(deepseek as HTMLElement).queryByText("Flash Free"),
    ).toBeNull();
    expect(
      within(deepseek as HTMLElement).queryByText("deepseek-v4-flash-free"),
    ).toBeNull();
    expect(within(list).getByText("Flash Free")).toBeTruthy();
  });

  it("picking a row from another channel updates both channel and model", async () => {
    vi.mocked(updateLlmModelProfile).mockResolvedValue({
      id: "user-mine",
      name: "办公",
      kind: "user",
      is_default: false,
      main: { origin: "byok", provider_id: "p1", model: "deepseek-v4-pro" },
      worker: null,
      background: null,
    });
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    // 办公 starts on p2 / gpt-4o — pick DeepSeek row in the unified list
    const trigger = document.getElementById("profile-main");
    expect(trigger?.textContent).toMatch(/GPT-4o/);
    const list = openModelPicker("profile-main");
    fireEvent.click(
      within(list).getByRole("option", { name: /DeepSeek V4 Pro/ }),
    );
    expect(document.getElementById("profile-main")?.textContent).toMatch(
      /DeepSeek V4 Pro/,
    );
    expect(screen.queryByText(/已切换渠道/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(updateLlmModelProfile).toHaveBeenCalledWith(
        "user-mine",
        expect.objectContaining({
          main: {
            origin: "byok",
            provider_id: "p1",
            model: "deepseek-v4-pro",
          },
        }),
      ),
    );
  });

  it("shows orphan provider and forces re-selection", () => {
    mockProviders(providersResponse());
    mockProfiles(
      profilesResponse({
        data: [
          {
            id: "user-mine",
            name: "办公",
            kind: "user",
            is_default: false,
            main: {
              origin: "byok",
              provider_id: "deleted-provider",
              model: "old-model",
            },
            worker: null,
            background: null,
          },
        ],
      }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(document.getElementById("profile-main-provider")).toBeNull();
    expect(screen.getByText(/原服务商已移除/)).toBeTruthy();
    expect(screen.getByText(/已移除的服务商/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "从目录选择" }));
    const list = screen.getByRole("listbox");
    expect(within(list).getByText(/已移除的服务商（需改选）/)).toBeTruthy();
    fireEvent.click(within(list).getByRole("option", { name: /GPT-4o/ }));
    expect(screen.queryByText(/原服务商已移除/)).toBeNull();
  });

  it("surfaces ADMIN_PRODUCT_FORBIDDEN instead of a generic load failure", () => {
    useLlmProvidersMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(
        403,
        JSON.stringify({
          error: {
            code: "ADMIN_PRODUCT_FORBIDDEN",
            message: "管理员账号请使用管理后台登录",
          },
        }),
      ),
    } as unknown as ReturnType<typeof useLlmProviders>);
    renderPage();
    expect(
      screen.getByText("此账号为管理员账号，请使用管理后台登录"),
    ).toBeTruthy();
    expect(screen.queryByText("加载失败，请重试")).toBeNull();
  });

  it("maps 404 load failure to client version-mismatch copy", () => {
    useLlmProvidersMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(404, "{}"),
    } as unknown as ReturnType<typeof useLlmProviders>);
    renderPage();
    expect(
      screen.getByText("当前客户端版本过旧，请到设置 · 关于检查更新"),
    ).toBeTruthy();
    expect(screen.queryByText("加载失败，请重试")).toBeNull();
  });

  it("saves create with a vision slot and clears it on edit", async () => {
    vi.mocked(createLlmModelProfile).mockResolvedValue({
      id: "user-vision",
      name: "识图组合",
      kind: "user",
      is_default: false,
      main: { origin: "byok", provider_id: "p1", model: "deepseek-v4-pro" },
      worker: null,
      background: null,
      vision: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
    });
    vi.mocked(updateLlmModelProfile).mockResolvedValue({
      id: "user-mine",
      name: "办公",
      kind: "user",
      is_default: false,
      main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
      worker: null,
      background: null,
      vision: null,
    });
    useModelsMock.mockReturnValue({
      data: {
        byok_configured: true,
        current: { id: "deepseek-v4-pro", origin: "byok", provider_id: "p1" },
        models: [
          {
            id: "deepseek-v4-pro",
            origin: "byok",
            display_name: "DeepSeek V4 Pro",
            vendor: "DeepSeek",
            provider_id: "p1",
            provider_label: "DeepSeek",
            capabilities: [],
            available: true,
          },
          {
            id: "gpt-4o",
            origin: "byok",
            display_name: "GPT-4o",
            vendor: "OpenAI",
            provider_id: "p2",
            provider_label: "OpenAI",
            capabilities: ["vision"],
            available: true,
          },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useModels>);
    mockProviders(providersResponse());
    mockProfiles(profilesResponse({ data: [] }));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    fireEvent.change(screen.getByLabelText(/名称/), {
      target: { value: "识图组合" },
    });
    fireEvent.click(screen.getByRole("button", { name: /高级 · 其他模型/ }));

    // vision catalog filtered: DeepSeek V4 Pro has no vision → absent from list
    const list = openModelPicker("profile-vision");
    expect(within(list).queryByText("DeepSeek V4 Pro")).toBeNull();
    expect(within(list).getByText("GPT-4o")).toBeTruthy();
    fireEvent.click(within(list).getByRole("option", { name: /GPT-4o/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(createLlmModelProfile).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "识图组合",
          vision: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
        }),
      ),
    );

    cleanup();
    mockProfiles(
      profilesResponse({
        data: [
          {
            id: "user-mine",
            name: "办公",
            kind: "user",
            is_default: false,
            main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
            worker: null,
            background: null,
            vision: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
          },
        ],
      }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(document.getElementById("profile-vision")?.textContent).toMatch(
      /GPT-4o/,
    );
    fireEvent.click(screen.getByRole("button", { name: "清除" }));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(updateLlmModelProfile).toHaveBeenCalledWith(
        "user-mine",
        expect.objectContaining({ vision: null }),
      ),
    );
  });

  it("falls back to full catalog for vision when no model advertises vision", () => {
    mockProviders(providersResponse());
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    fireEvent.click(screen.getByRole("button", { name: /高级 · 其他模型/ }));
    const list = openModelPicker("profile-vision");
    expect(within(list).getByText("DeepSeek V4 Pro")).toBeTruthy();
    expect(within(list).getByText("GPT-4o")).toBeTruthy();
  });

  it("copies vision slot when duplicating a profile", async () => {
    vi.mocked(createLlmModelProfile).mockResolvedValue({
      id: "user-copy",
      name: "办公 副本",
      kind: "user",
      is_default: false,
      main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
      vision: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
    });
    mockProviders(providersResponse());
    mockProfiles(
      profilesResponse({
        data: [
          {
            id: "user-mine",
            name: "办公",
            kind: "user",
            is_default: false,
            main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
            worker: null,
            background: null,
            vision: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
          },
        ],
      }),
    );
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "复制" }));
    await waitFor(() =>
      expect(createLlmModelProfile).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "办公 副本",
          vision: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
        }),
      ),
    );
  });
});
