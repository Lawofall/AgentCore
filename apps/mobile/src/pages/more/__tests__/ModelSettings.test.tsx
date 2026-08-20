// @vitest-environment jsdom
/**
 * Render + interaction tests for 设置·模型配置 — providers + 模型组合 section.
 */
import type { LlmProvidersResponse } from "@/api/llmProviders";
import {
  deleteLlmProvider,
  listLlmProviders,
  testLlmProvider,
} from "@/api/llmProviders";
import type { LlmModelProfileView } from "@/api/modelProfiles";
import {
  createModelProfile,
  deleteModelProfile,
  listModelProfiles,
  setDefaultModelProfile,
  updateModelProfile,
} from "@/api/modelProfiles";
import type { ModelCatalog } from "@/api/models";
import { ModelSettings } from "@/pages/more/ModelSettings";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/llmProviders", () => ({
  listLlmProviders: vi.fn(),
  testLlmProvider: vi.fn(),
  deleteLlmProvider: vi.fn(),
  createLlmProvider: vi.fn(),
  updateLlmProvider: vi.fn(),
}));

vi.mock("@/api/modelProfiles", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/modelProfiles")>();
  return {
    ...actual,
    listModelProfiles: vi.fn(),
    createModelProfile: vi.fn(),
    updateModelProfile: vi.fn(),
    deleteModelProfile: vi.fn(),
    setDefaultModelProfile: vi.fn(),
  };
});

vi.mock("@/components/conversations", () => ({ ConfirmDialog: () => null }));

vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return { ...actual, useNavigate: () => vi.fn() };
});

const CATALOG: ModelCatalog = {
  byok_configured: true,
  current: {
    id: "deepseek-v4-pro",
    origin: "byok",
    provider_id: "prov-deepseek",
  },
  models: [
    {
      id: "platform-flash",
      origin: "platform",
      display_name: "Flash (平台)",
      vendor: "Platform",
      capabilities: [],
      context_length: null,
      price: null,
      available: true,
    },
    {
      id: "deepseek-v4-pro",
      origin: "byok",
      provider_id: "prov-deepseek",
      provider_label: "DeepSeek",
      display_name: "DeepSeek V4 Pro",
      vendor: "DeepSeek",
      capabilities: [],
      context_length: null,
      price: null,
      available: true,
    },
    {
      id: "gpt-4o",
      origin: "byok",
      provider_id: "prov-openai",
      provider_label: "OpenAI",
      display_name: "GPT-4o",
      vendor: "OpenAI",
      capabilities: ["vision"],
      context_length: null,
      price: null,
      available: true,
    },
  ],
};

/** Per-test catalog override (null → default CATALOG). Hoisted for vi.mock. */
const catalogState = vi.hoisted(() => ({
  override: null as ModelCatalog | null,
}));

vi.mock("@/api/models", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/models")>();
  return {
    ...actual,
    useModels: () => ({
      data: catalogState.override ?? CATALOG,
      loading: false,
      error: null,
      refetch: vi.fn(),
    }),
  };
});

const mockList = vi.mocked(listLlmProviders);
const mockListProfiles = vi.mocked(listModelProfiles);
const mockSetDefault = vi.mocked(setDefaultModelProfile);
const mockCreateProfile = vi.mocked(createModelProfile);
const mockUpdateProfile = vi.mocked(updateModelProfile);
vi.mocked(testLlmProvider);
vi.mocked(deleteLlmProvider);
vi.mocked(deleteModelProfile);

const SYSTEM_52: LlmModelProfileView = {
  id: "00000000-0000-4000-8000-000000000011",
  name: "GLM-5.2",
  kind: "system",
  main: { origin: "platform", model: "glm-5.2", provider_id: null },
  worker: null,
  background: null,
  is_default: true,
};

const USER_PROFILE: LlmModelProfileView = {
  id: "prof-user-1",
  name: "写作强档",
  kind: "user",
  main: {
    origin: "byok",
    model: "deepseek-v4-pro",
    provider_id: "prov-deepseek",
  },
  worker: { origin: "byok", model: "gpt-4o", provider_id: "prov-openai" },
  background: null,
  is_default: false,
};

function makeProviders(
  overrides: Partial<LlmProvidersResponse> = {},
): LlmProvidersResponse {
  return {
    providers: [
      {
        id: "prov-deepseek",
        label: "DeepSeek",
        base_url: "https://api.deepseek.com",
        default_model: "deepseek-v4-pro",
        status: "active",
        masked_key: "sk-…abcd",
        supports_tools: true,
      },
      {
        id: "prov-openai",
        label: "OpenAI",
        base_url: "https://api.openai.com/v1",
        default_model: "gpt-4o",
        status: "unchecked",
        masked_key: "sk-…wxyz",
      },
    ],
    billing_mode: "platform",
    platform_available: true,
    platform_model: "deepseek-v4-pro",
    ...overrides,
  };
}

function stubProfiles(data: LlmModelProfileView[], defaultId?: string | null) {
  mockListProfiles.mockResolvedValue({
    data,
    default_model_profile_id: defaultId ?? data.find((p) => p.is_default)?.id,
  });
}

afterEach(cleanup);
beforeEach(() => {
  catalogState.override = null;
  mockList.mockReset();
  mockListProfiles.mockReset();
  mockSetDefault.mockReset();
  mockCreateProfile.mockReset();
  mockUpdateProfile.mockReset();
});

/** Open「高级 · 分槽覆盖」so Worker / 后台 / 识图 controls are mounted. */
function openAdvancedSlots() {
  fireEvent.click(screen.getByRole("button", { name: /高级 · 分槽覆盖/ }));
  expect(screen.getByText("Worker 模型")).toBeTruthy();
}

describe("ModelSettings (profiles + providers)", () => {
  it("renders provider cards and the model-profiles section", async () => {
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52, USER_PROFILE], SYSTEM_52.id);
    render(<ModelSettings />);

    await waitFor(() => expect(screen.getByText("DeepSeek")).toBeTruthy());
    expect(screen.getByText("OpenAI")).toBeTruthy();
    expect(screen.getByText("api.deepseek.com")).toBeTruthy();
    expect(screen.getByText("测试用模型 deepseek-v4-pro")).toBeTruthy();
    expect(screen.getByText("测试用模型 gpt-4o")).toBeTruthy();
    expect(screen.queryByText("模型 deepseek-v4-pro")).toBeNull();
    expect(screen.getAllByTestId("provider-card")).toHaveLength(2);
    expect(screen.getByTestId("profiles-section")).toBeTruthy();
    expect(screen.queryByText(/多人协作（委派）对工具调用要求较高/)).toBeNull();
    expect(screen.getByText("GLM-5.2")).toBeTruthy();
    expect(screen.getByText("写作强档")).toBeTruthy();
    expect(screen.getByText("账号默认")).toBeTruthy();
    expect(screen.getByText("DeepSeek V4 Pro · GPT-4o")).toBeTruthy();
  });

  it("shows the platform-credit note when the deployment offers platform models", async () => {
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52], SYSTEM_52.id);
    render(<ModelSettings />);
    await waitFor(() =>
      expect(screen.getByText(/不接入也可用平台额度直接对话/)).toBeTruthy(),
    );
  });

  it("when platform is off, settings desc guides to jiurelay or providers", async () => {
    mockList.mockResolvedValue(
      makeProviders({
        billing_mode: "byok",
        platform_available: false,
        platform_model: null,
      }),
    );
    stubProfiles([USER_PROFILE], USER_PROFILE.id);
    render(<ModelSettings />);
    await waitFor(() =>
      expect(
        screen.getByText(/需自行在 jiurelay 免费配额度或接入服务商/),
      ).toBeTruthy(),
    );
    expect(screen.queryByText(/不接入也可用平台额度直接对话/)).toBeNull();
  });

  it("sets the account default combination", async () => {
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52, USER_PROFILE], SYSTEM_52.id);
    mockSetDefault.mockResolvedValue({ ...USER_PROFILE, is_default: true });
    render(<ModelSettings />);

    await waitFor(() => expect(screen.getByText("写作强档")).toBeTruthy());
    fireEvent.click(screen.getByText("设为默认"));
    await waitFor(() =>
      expect(mockSetDefault).toHaveBeenCalledWith(USER_PROFILE.id),
    );
  });

  it("opens the new-profile form with optional slots collapsed under 高级", async () => {
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52], SYSTEM_52.id);
    render(<ModelSettings />);

    await waitFor(() => expect(screen.getByTestId("profile-new")).toBeTruthy());
    fireEvent.click(screen.getByTestId("profile-new"));

    await screen.findByTestId("profile-form");
    expect(screen.getByText("主模型（必填）")).toBeTruthy();
    expect(screen.getByText("高级 · 分槽覆盖")).toBeTruthy();
    expect(
      screen.getByText("Worker/后台：跟随主模型 · 识图：不配置"),
    ).toBeTruthy();
    expect(screen.queryByText("Worker 模型")).toBeNull();
    expect(screen.queryByText("后台任务模型")).toBeNull();
    expect(screen.queryByText("识图模型（可选）")).toBeNull();

    openAdvancedSlots();
    expect(screen.getByText("Worker 模型")).toBeTruthy();
    expect(screen.getByText("后台任务模型")).toBeTruthy();
    expect(screen.getByText("识图模型（可选）")).toBeTruthy();
    expect(screen.getAllByText("跟随主模型").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("不配置")).toBeTruthy();
    expect(screen.getByText(/辩论用主模型/)).toBeTruthy();
    expect(screen.getByText(/标题、记忆等后台任务/)).toBeTruthy();
    expect(screen.getByText(/主模型不能看图时再配/)).toBeTruthy();
    expect(screen.queryByText(/VISION_/)).toBeNull();
  });

  it("opens the new-profile form and creates a combination", async () => {
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52], SYSTEM_52.id);
    mockCreateProfile.mockResolvedValue(USER_PROFILE);
    render(<ModelSettings />);

    await waitFor(() => expect(screen.getByTestId("profile-new")).toBeTruthy());
    fireEvent.click(screen.getByTestId("profile-new"));

    const name = (await screen.findByLabelText("名称")) as HTMLInputElement;
    fireEvent.change(name, { target: { value: "写作强档" } });
    fireEvent.change(screen.getByTestId("profile-main-provider"), {
      target: { value: "prov-deepseek" },
    });
    fireEvent.change(screen.getByTestId("profile-main-model"), {
      target: { value: "deepseek-v4-pro" },
    });
    fireEvent.click(screen.getByText("保存"));

    await waitFor(() =>
      expect(mockCreateProfile).toHaveBeenCalledWith({
        name: "写作强档",
        main: {
          origin: "byok",
          provider_id: "prov-deepseek",
          model: "deepseek-v4-pro",
        },
        worker: null,
        background: null,
        vision: null,
        set_as_default: false,
      }),
    );
  });

  it("creates with a vision slot and prefers vision-capable catalog rows", async () => {
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52], SYSTEM_52.id);
    mockCreateProfile.mockResolvedValue({
      ...USER_PROFILE,
      vision: {
        origin: "byok",
        provider_id: "prov-openai",
        model: "gpt-4o",
      },
    });
    render(<ModelSettings />);

    await waitFor(() => expect(screen.getByTestId("profile-new")).toBeTruthy());
    fireEvent.click(screen.getByTestId("profile-new"));

    fireEvent.change(await screen.findByLabelText("名称"), {
      target: { value: "识图组合" },
    });
    fireEvent.change(screen.getByTestId("profile-main-provider"), {
      target: { value: "prov-deepseek" },
    });
    fireEvent.change(screen.getByTestId("profile-main-model"), {
      target: { value: "deepseek-v4-pro" },
    });

    openAdvancedSlots();
    expect(screen.getByText(/主模型不能看图时再配/)).toBeTruthy();
    expect(screen.queryByText(/当前主模型标有视觉/)).toBeNull();

    // 识图槽始终可见 combobox；切到 OpenAI 后 datalist 仅含 vision 目录项
    fireEvent.change(screen.getByTestId("profile-vision-provider"), {
      target: { value: "prov-openai" },
    });
    const visionList = document.getElementById("profile-vision-suggestions");
    expect(visionList).toBeTruthy();
    const visionValues = [
      ...((visionList as HTMLDataListElement).querySelectorAll("option") ?? []),
    ].map((o) => (o as HTMLOptionElement).value);
    expect(visionValues).toContain("gpt-4o");
    expect(visionValues).not.toContain("deepseek-v4-pro");

    fireEvent.change(screen.getByTestId("profile-main-provider"), {
      target: { value: "prov-openai" },
    });
    fireEvent.change(screen.getByTestId("profile-main-model"), {
      target: { value: "gpt-4o" },
    });
    expect(
      screen.getByText(/当前主模型标有视觉，贴图优先走主模型/),
    ).toBeTruthy();
    // 切回无视觉主模型后再选识图槽，避免把主模型 vision 能力混进 create body
    fireEvent.change(screen.getByTestId("profile-main-provider"), {
      target: { value: "prov-deepseek" },
    });
    fireEvent.change(screen.getByTestId("profile-main-model"), {
      target: { value: "deepseek-v4-pro" },
    });

    fireEvent.change(screen.getByTestId("profile-vision-provider"), {
      target: { value: "prov-openai" },
    });
    fireEvent.change(screen.getByTestId("profile-vision-model"), {
      target: { value: "gpt-4o" },
    });
    fireEvent.click(screen.getByText("保存"));

    await waitFor(() =>
      expect(mockCreateProfile).toHaveBeenCalledWith({
        name: "识图组合",
        main: {
          origin: "byok",
          provider_id: "prov-deepseek",
          model: "deepseek-v4-pro",
        },
        worker: null,
        background: null,
        vision: {
          origin: "byok",
          provider_id: "prov-openai",
          model: "gpt-4o",
        },
        set_as_default: false,
      }),
    );
  });

  it("auto-expands 高级 when editing a profile with slot overrides", async () => {
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52, USER_PROFILE], SYSTEM_52.id);
    render(<ModelSettings />);

    await waitFor(() =>
      expect(
        screen.getByTestId(`profile-card-${USER_PROFILE.id}`),
      ).toBeTruthy(),
    );
    const card = screen.getByTestId(`profile-card-${USER_PROFILE.id}`);
    fireEvent.click(
      card.querySelector("button.btn-outline") as HTMLButtonElement,
    );

    await screen.findByTestId("profile-form");
    const advanced = screen.getByTestId("profile-advanced");
    expect(advanced.querySelector('[aria-expanded="true"]')).toBeTruthy();
    expect(screen.getByText("Worker 模型")).toBeTruthy();
    expect(
      (screen.getByTestId("profile-worker-model") as HTMLInputElement).value,
    ).toBe("gpt-4o");
    expect(
      screen.queryByText("Worker：gpt-4o · 后台：跟随主模型 · 识图：不配置"),
    ).toBeNull();
  });

  it("clears vision on edit when set to 不配置", async () => {
    const withVision: LlmModelProfileView = {
      ...USER_PROFILE,
      vision: {
        origin: "byok",
        provider_id: "prov-openai",
        model: "gpt-4o",
      },
    };
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52, withVision], SYSTEM_52.id);
    mockUpdateProfile.mockResolvedValue({ ...withVision, vision: null });
    render(<ModelSettings />);

    await waitFor(() =>
      expect(screen.getByTestId(`profile-card-${withVision.id}`)).toBeTruthy(),
    );
    const card = screen.getByTestId(`profile-card-${withVision.id}`);
    fireEvent.click(
      card.querySelector("button.btn-outline") as HTMLButtonElement,
    );

    // 有 vision 覆盖 → 高级默认展开
    await screen.findByTestId("profile-vision-combobox");
    expect(
      (screen.getByTestId("profile-vision-provider") as HTMLSelectElement)
        .value,
    ).toBe("prov-openai");
    expect(
      (screen.getByTestId("profile-vision-model") as HTMLInputElement).value,
    ).toBe("gpt-4o");
    fireEvent.click(screen.getByTestId("profile-vision-clear"));
    fireEvent.click(screen.getByText("保存"));

    await waitFor(() =>
      expect(mockUpdateProfile).toHaveBeenCalledWith(
        withVision.id,
        expect.objectContaining({ vision: null }),
      ),
    );
  });

  it("when BYOK exists but catalog empty, freeform stays available and advanced slots stay enabled", async () => {
    catalogState.override = {
      byok_configured: true,
      current: {
        id: "orphan-model",
        origin: "byok",
        provider_id: "gone-provider",
      },
      models: [],
    };
    mockList.mockResolvedValue(
      makeProviders({
        providers: [
          {
            id: "prov-deepseek",
            label: "DeepSeek",
            base_url: "https://api.deepseek.com",
            default_model: "",
            status: "active",
            masked_key: "sk-…abcd",
            supports_tools: true,
          },
        ],
        platform_available: false,
        platform_model: null,
      }),
    );
    stubProfiles([]);
    render(<ModelSettings />);

    await waitFor(() => expect(screen.getByTestId("profile-new")).toBeTruthy());
    expect(screen.queryByText(/测试用模型/)).toBeNull();
    fireEvent.click(screen.getByTestId("profile-new"));
    await screen.findByTestId("profile-form");
    expect(screen.getByTestId("profile-main-combobox")).toBeTruthy();
    expect(screen.queryByText(/暂无可用模型/)).toBeNull();
    openAdvancedSlots();
    expect(
      (screen.getByTestId("profile-worker-model") as HTMLInputElement).disabled,
    ).toBe(false);
    expect(
      (screen.getByTestId("profile-background-model") as HTMLInputElement)
        .disabled,
    ).toBe(false);
    expect(
      (screen.getByTestId("profile-vision-model") as HTMLInputElement).disabled,
    ).toBe(false);
  });

  it("saves a hand-filled custom BYOK model id without 自定义… hop", async () => {
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52], SYSTEM_52.id);
    mockCreateProfile.mockResolvedValue({
      ...USER_PROFILE,
      name: "火山接入点",
      main: {
        origin: "byok",
        provider_id: "prov-deepseek",
        model: "ep-my-endpoint",
      },
      worker: null,
    });
    render(<ModelSettings />);

    await waitFor(() => expect(screen.getByTestId("profile-new")).toBeTruthy());
    fireEvent.click(screen.getByTestId("profile-new"));

    fireEvent.change(await screen.findByLabelText("名称"), {
      target: { value: "火山接入点" },
    });
    // 有 BYOK 时 combobox 始终可见，无需点「自定义…」
    expect(screen.getByTestId("profile-main-combobox")).toBeTruthy();
    expect(screen.queryByText("自定义…")).toBeNull();
    fireEvent.change(screen.getByTestId("profile-main-provider"), {
      target: { value: "prov-deepseek" },
    });
    fireEvent.change(screen.getByTestId("profile-main-model"), {
      target: { value: "ep-my-endpoint" },
    });
    fireEvent.click(screen.getByText("保存"));

    await waitFor(() =>
      expect(mockCreateProfile).toHaveBeenCalledWith({
        name: "火山接入点",
        main: {
          origin: "byok",
          provider_id: "prov-deepseek",
          model: "ep-my-endpoint",
        },
        worker: null,
        background: null,
        vision: null,
        set_as_default: false,
      }),
    );
  });

  it("echoes a saved custom BYOK model when editing", async () => {
    const customProfile: LlmModelProfileView = {
      ...USER_PROFILE,
      main: {
        origin: "byok",
        provider_id: "prov-deepseek",
        model: "ep-saved-custom",
      },
      worker: null,
    };
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52, customProfile], SYSTEM_52.id);
    render(<ModelSettings />);

    await waitFor(() =>
      expect(
        screen.getByTestId(`profile-card-${customProfile.id}`),
      ).toBeTruthy(),
    );
    const card = screen.getByTestId(`profile-card-${customProfile.id}`);
    const editBtn = card.querySelector("button.btn-outline");
    expect(editBtn).toBeTruthy();
    fireEvent.click(editBtn as HTMLButtonElement);

    await screen.findByTestId("profile-main-combobox");
    expect(
      (screen.getByTestId("profile-main-provider") as HTMLSelectElement).value,
    ).toBe("prov-deepseek");
    expect(
      (screen.getByTestId("profile-main-model") as HTMLInputElement).value,
    ).toBe("ep-saved-custom");
  });

  it("platform-only catalog uses pure select without free-text", async () => {
    mockList.mockResolvedValue(
      makeProviders({
        providers: [],
        platform_available: true,
        platform_model: "platform-flash",
      }),
    );
    stubProfiles([SYSTEM_52], SYSTEM_52.id);
    render(<ModelSettings />);

    await waitFor(() => expect(screen.getByTestId("profile-new")).toBeTruthy());
    fireEvent.click(screen.getByTestId("profile-new"));

    await screen.findByTestId("profile-main-select");
    expect(screen.queryByTestId("profile-main-combobox")).toBeNull();
    expect(screen.queryByTestId("profile-main-model")).toBeNull();
    const mainSelect = screen.getByTestId(
      "profile-main-select",
    ) as HTMLSelectElement;
    expect(
      [...mainSelect.options].some(
        (o) => o.value === "__platform__::platform-flash",
      ),
    ).toBe(true);
  });

  it("shows why an unavailable BYOK catalog model cannot be selected", async () => {
    catalogState.override = {
      ...CATALOG,
      models: [
        ...CATALOG.models,
        {
          id: "grok-4.5",
          origin: "byok",
          provider_id: "prov-deepseek",
          provider_label: "DeepSeek",
          display_name: "Grok 4.5",
          vendor: "xAI",
          capabilities: [],
          context_length: null,
          price: null,
          available: false,
          unavailable_reason: {
            code: "upstream_protocol_unsupported",
            required_protocol: "openai_responses",
          },
        },
      ],
    };
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52], SYSTEM_52.id);
    render(<ModelSettings />);

    await waitFor(() => expect(screen.getByTestId("profile-new")).toBeTruthy());
    fireEvent.click(screen.getByTestId("profile-new"));
    await screen.findByTestId("profile-main-combobox");
    fireEvent.change(screen.getByTestId("profile-main-provider"), {
      target: { value: "prov-deepseek" },
    });
    expect(
      screen.getByTestId("profile-main-unavailable-grok-4.5").textContent,
    ).toMatch(/\/responses/);
  });

  it("platform select greys unavailable rows with the protocol reason", async () => {
    catalogState.override = {
      byok_configured: false,
      current: { id: "platform-flash", origin: "platform" },
      models: [
        CATALOG.models[0],
        {
          id: "grok-4.5",
          origin: "platform",
          display_name: "Grok 4.5",
          vendor: "xAI",
          capabilities: [],
          context_length: null,
          price: null,
          available: false,
          unavailable_reason: {
            code: "upstream_protocol_unsupported",
            required_protocol: "openai_responses",
          },
        },
      ],
    };
    mockList.mockResolvedValue(
      makeProviders({
        providers: [],
        platform_available: true,
        platform_model: "platform-flash",
      }),
    );
    stubProfiles([SYSTEM_52], SYSTEM_52.id);
    render(<ModelSettings />);

    await waitFor(() => expect(screen.getByTestId("profile-new")).toBeTruthy());
    fireEvent.click(screen.getByTestId("profile-new"));
    const mainSelect = (await screen.findByTestId(
      "profile-main-select",
    )) as HTMLSelectElement;
    const grok = [...mainSelect.options].find((o) =>
      o.value.includes("grok-4.5"),
    );
    expect(grok?.disabled).toBe(true);
    expect(grok?.textContent).toMatch(/\/responses/);
  });

  it("does not offer edit/delete on system presets", async () => {
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52], SYSTEM_52.id);
    render(<ModelSettings />);

    await waitFor(() =>
      expect(screen.getByTestId(`profile-card-${SYSTEM_52.id}`)).toBeTruthy(),
    );
    const card = screen.getByTestId(`profile-card-${SYSTEM_52.id}`);
    expect(card.textContent).toContain("预置");
    expect(card.textContent).not.toContain("编辑");
    expect(card.textContent).not.toContain("删除");
  });

  it("shows 后台 / 识图 in the list summary when those slots are set", async () => {
    const withSlots: LlmModelProfileView = {
      ...USER_PROFILE,
      background: {
        origin: "byok",
        model: "gpt-4o",
        provider_id: "prov-openai",
      },
      vision: {
        origin: "byok",
        model: "gpt-4o",
        provider_id: "prov-openai",
      },
    };
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52, withSlots], SYSTEM_52.id);
    render(<ModelSettings />);

    await waitFor(() =>
      expect(
        screen.getByText(
          "DeepSeek V4 Pro · GPT-4o · 后台 GPT-4o · 识图 GPT-4o",
        ),
      ).toBeTruthy(),
    );
  });

  it("does not leak another profile's foreign SKU into DeepSeek suggestions", async () => {
    const foreign: LlmModelProfileView = {
      id: "prof-foreign",
      name: "中转免费档",
      kind: "user",
      main: {
        origin: "byok",
        // Not a DeepSeek official SKU — must not appear under DeepSeek when editing USER_PROFILE.
        model: "deepseek-v4-flash-free",
        provider_id: "prov-openai",
      },
      worker: null,
      background: null,
      is_default: false,
    };
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52, USER_PROFILE, foreign], SYSTEM_52.id);
    render(<ModelSettings />);

    await waitFor(() =>
      expect(
        screen.getByTestId(`profile-card-${USER_PROFILE.id}`),
      ).toBeTruthy(),
    );
    const card = screen.getByTestId(`profile-card-${USER_PROFILE.id}`);
    fireEvent.click(
      card.querySelector("button.btn-outline") as HTMLButtonElement,
    );
    await screen.findByTestId("profile-main-combobox");

    fireEvent.change(screen.getByTestId("profile-main-provider"), {
      target: { value: "prov-deepseek" },
    });
    const list = document.getElementById("profile-main-suggestions");
    expect(list).toBeTruthy();
    const values = [
      ...((list as HTMLDataListElement).querySelectorAll("option") ?? []),
    ].map((o) => (o as HTMLOptionElement).value);
    expect(values).toContain("deepseek-v4-pro");
    expect(values).not.toContain("deepseek-v4-flash-free");
  });

  it("clears a non-matching model id when switching providers (no silent mismatch save)", async () => {
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52], SYSTEM_52.id);
    mockCreateProfile.mockResolvedValue(USER_PROFILE);
    render(<ModelSettings />);

    await waitFor(() => expect(screen.getByTestId("profile-new")).toBeTruthy());
    fireEvent.click(screen.getByTestId("profile-new"));
    await screen.findByTestId("profile-form");

    fireEvent.change(screen.getByLabelText("名称"), {
      target: { value: "错配拦截" },
    });
    fireEvent.change(screen.getByTestId("profile-main-provider"), {
      target: { value: "prov-deepseek" },
    });
    fireEvent.change(screen.getByTestId("profile-main-model"), {
      target: { value: "deepseek-v4-pro" },
    });
    expect(
      (screen.getByTestId("profile-main-model") as HTMLInputElement).value,
    ).toBe("deepseek-v4-pro");

    // Switch channel: foreign id must not remain paired with OpenAI.
    fireEvent.change(screen.getByTestId("profile-main-provider"), {
      target: { value: "prov-openai" },
    });
    const modelAfter = (
      screen.getByTestId("profile-main-model") as HTMLInputElement
    ).value;
    expect(modelAfter).not.toBe("deepseek-v4-pro");
    expect(modelAfter).toBe("gpt-4o");

    fireEvent.click(screen.getByText("保存"));
    await waitFor(() =>
      expect(mockCreateProfile).toHaveBeenCalledWith(
        expect.objectContaining({
          main: {
            origin: "byok",
            provider_id: "prov-openai",
            model: "gpt-4o",
          },
        }),
      ),
    );
    expect(mockCreateProfile).not.toHaveBeenCalledWith(
      expect.objectContaining({
        main: {
          origin: "byok",
          provider_id: "prov-openai",
          model: "deepseek-v4-pro",
        },
      }),
    );
  });

  it("clears optional slot model on provider switch instead of keeping a foreign id", async () => {
    mockList.mockResolvedValue(makeProviders());
    stubProfiles([SYSTEM_52, USER_PROFILE], SYSTEM_52.id);
    render(<ModelSettings />);

    await waitFor(() =>
      expect(
        screen.getByTestId(`profile-card-${USER_PROFILE.id}`),
      ).toBeTruthy(),
    );
    const card = screen.getByTestId(`profile-card-${USER_PROFILE.id}`);
    fireEvent.click(
      card.querySelector("button.btn-outline") as HTMLButtonElement,
    );
    await screen.findByTestId("profile-worker-combobox");

    expect(
      (screen.getByTestId("profile-worker-model") as HTMLInputElement).value,
    ).toBe("gpt-4o");
    fireEvent.change(screen.getByTestId("profile-worker-provider"), {
      target: { value: "prov-deepseek" },
    });
    expect(
      (screen.getByTestId("profile-worker-model") as HTMLInputElement).value,
    ).toBe("");
  });

  it("surfaces ADMIN_PRODUCT_FORBIDDEN instead of a generic load failure", async () => {
    mockList.mockRejectedValue(
      new Error("此账号为管理员账号，请使用管理后台登录"),
    );
    stubProfiles([]);
    render(<ModelSettings />);
    await waitFor(() =>
      expect(
        screen.getByText("此账号为管理员账号，请使用管理后台登录"),
      ).toBeTruthy(),
    );
    expect(screen.queryByText("加载失败，请重试")).toBeNull();
    const line = screen
      .getByText("此账号为管理员账号，请使用管理后台登录")
      .closest(".error");
    expect(line?.className).toBe("error");
    expect(line?.className).not.toMatch(/\b(bar|inline-actions|needs-you)\b/);
  });
});
