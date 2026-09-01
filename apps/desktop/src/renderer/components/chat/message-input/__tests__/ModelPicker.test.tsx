// @vitest-environment jsdom
/**
 * Tests for the input-box model profile picker (模型组合).
 *
 * Lists system + user combinations (no live-follow row), PATCHes
 * `model_profile_id`, inherits last-used profile on a fresh chat, and links to
 * settings for management.
 */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useLlmModelProfiles", () => ({
  useLlmModelProfiles: vi.fn(),
}));
vi.mock("@/hooks/useModels", () => ({ useModels: vi.fn() }));
vi.mock("@/hooks/useLlmProviders", () => ({
  useLlmProviders: vi.fn(),
}));
vi.mock("@/hooks/useConversations", () => ({
  useConversations: vi.fn(() => []),
  patchConversationCache: vi.fn(),
}));
vi.mock("@/services/conversations", () => ({
  setConversationModelProfile: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({
  notifySuccess: vi.fn(),
  notifyError: vi.fn(),
}));

import { TooltipProvider } from "@/components/ui/tooltip";
import {
  patchConversationCache,
  useConversations,
} from "@/hooks/useConversations";
import { useLlmModelProfiles } from "@/hooks/useLlmModelProfiles";
import { useLlmProviders } from "@/hooks/useLlmProviders";
import { useModels } from "@/hooks/useModels";
import { useComposerProfileDraftStore } from "@/lib/composerModelProfile";
import { __setUiStorageBackendForTests } from "@/lib/uiStorage";
import { setConversationModelProfile } from "@/services/conversations";
import type { LlmModelProfileListResponse } from "@/services/llmModelProfiles";
import type { LlmProvidersResponse } from "@/services/llmProviders";
import { setLastUsedProfileId } from "@/services/models";
import { useConversationStore } from "@/stores/conversation";
import type { Conversation } from "@/stores/conversation";
import { ModelPicker } from "../ModelPicker";

const useProfilesMock = vi.mocked(useLlmModelProfiles);
const useModelsMock = vi.mocked(useModels);
const useLlmProvidersMock = vi.mocked(useLlmProviders);
const useConversationsMock = vi.mocked(useConversations);
const setProfileMock = vi.mocked(setConversationModelProfile);

function profiles(
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
        id: "user-research",
        name: "研究",
        kind: "user",
        is_default: false,
        main: { origin: "byok", provider_id: "p2", model: "gpt-4o" },
        worker: { origin: "byok", provider_id: "p1", model: "deepseek-v4-pro" },
        background: null,
      },
      {
        id: "user-mine",
        name: "办公",
        kind: "user",
        is_default: false,
        main: { origin: "platform", provider_id: null, model: "flash" },
        worker: null,
        background: null,
      },
    ],
    ...over,
  };
}

function mockProfiles(data: LlmModelProfileListResponse | undefined): void {
  useProfilesMock.mockReturnValue({
    data,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useLlmModelProfiles>);
}

function mockProviders(over: Partial<LlmProvidersResponse> = {}): void {
  useLlmProvidersMock.mockReturnValue({
    data: {
      providers: [],
      billing_mode: "byok",
      platform_available: false,
      platform_model: null,
      ...over,
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useLlmProviders>);
}

function conv(partial: Partial<Conversation> & { id: string }): Conversation {
  return {
    title: "T",
    updatedAt: "",
    messageCount: 0,
    lastMessagePreview: null,
    ...partial,
  } as Conversation;
}

function renderPicker() {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <ModelPicker />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  const store = new Map<string, string>();
  __setUiStorageBackendForTests({
    getItem: (k) => store.get(k) ?? null,
    setItem: (k, v) => {
      store.set(k, v);
    },
    removeItem: (k) => {
      store.delete(k);
    },
    keys: () => [...store.keys()],
  });
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useComposerProfileDraftStore.setState({ profileId: null });
  useConversationsMock.mockReturnValue([]);
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
          capabilities: [],
          available: true,
        },
        {
          id: "gpt-4o",
          origin: "byok",
          display_name: "GPT-4o",
          vendor: "OpenAI",
          provider_id: "p2",
          capabilities: [],
          available: true,
        },
        {
          id: "flash",
          origin: "platform",
          display_name: "Flash",
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
  mockProviders();
  setProfileMock.mockReset();
  vi.mocked(patchConversationCache).mockReset();
});

afterEach(() => {
  __setUiStorageBackendForTests(null);
  cleanup();
});

describe("ModelPicker", () => {
  it("shows the account default profile on a fresh conversation", () => {
    mockProfiles(profiles());
    renderPicker();
    expect(screen.getByText("GLM-5.2")).toBeTruthy();
    // 触发器与下拉都只有组合名；主 · Worker 摘要只在 tooltip，不占下拉行。
    expect(screen.queryByText(/DeepSeek V4 Pro · 跟随主模型/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /模型组合：/ }));
    expect(screen.getByText("系统预置")).toBeTruthy();
    expect(screen.queryByText(/DeepSeek V4 Pro · 跟随主模型/)).toBeNull();
    expect(screen.queryByText(/DeepSeek V4 Pro · GPT-4o/)).toBeNull();
  });

  it("shows the conversation's profile override over the account default", () => {
    useConversationStore.setState({ currentConversationId: "c1", byId: {} });
    useConversationsMock.mockReturnValue([
      conv({ id: "c1", modelProfileId: "user-research" }),
    ]);
    mockProfiles(profiles());
    renderPicker();
    expect(screen.getByText("研究")).toBeTruthy();
  });

  it("marks the collapsed chip as 预置 only for system profiles", () => {
    mockProfiles(profiles());
    const { unmount } = renderPicker();
    // 折叠态（下拉未展开，「系统预置」分组标题不在）就能看出跑的是平台预置组合。
    expect(screen.queryByText("系统预置")).toBeNull();
    expect(screen.getByText("预置")).toBeTruthy();
    expect(screen.getByLabelText("模型组合：GLM-5.2（预置）")).toBeTruthy();
    unmount();

    // 用户自建组合不挂标识。
    useConversationStore.setState({ currentConversationId: "c1", byId: {} });
    useConversationsMock.mockReturnValue([
      conv({ id: "c1", modelProfileId: "user-research" }),
    ]);
    renderPicker();
    expect(screen.getByText("研究")).toBeTruthy();
    expect(screen.queryByText("预置")).toBeNull();
  });

  it("lists system + user profiles and a manage link without follow-default", () => {
    mockProfiles(profiles());
    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: /模型组合：/ }));
    expect(screen.queryByText("跟随账号默认")).toBeNull();
    expect(screen.getByText("系统预置")).toBeTruthy();
    expect(screen.getByText("我的组合")).toBeTruthy();
    expect(screen.getByText("管理组合…")).toBeTruthy();
    const panel = screen.getByRole("button", {
      name: /管理组合/,
    }).parentElement;
    const panelClasses = panel?.className.split(/\s+/) ?? [];
    expect(panelClasses).toEqual(
      expect.arrayContaining(["w-max", "min-w-52", "max-w-72"]),
    );
    expect(panelClasses).not.toContain("w-72");
    // No bare model catalog rows.
    expect(screen.queryByText("自带 Key")).toBeNull();
  });

  it("persists the pick via PATCH model_profile_id", async () => {
    useConversationStore.setState({ currentConversationId: "c1", byId: {} });
    useConversationsMock.mockReturnValue([
      conv({ id: "c1", modelProfileId: null }),
    ]);
    mockProfiles(profiles());
    setProfileMock.mockResolvedValue(
      conv({ id: "c1", modelProfileId: "user-mine" }),
    );

    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: /模型组合：/ }));
    fireEvent.click(screen.getByText("办公"));

    await waitFor(() =>
      expect(setProfileMock).toHaveBeenCalledWith("c1", "user-mine"),
    );
    expect(patchConversationCache).toHaveBeenCalledWith("c1", {
      modelProfileId: "user-mine",
    });
  });

  it("inherits the last-used profile id as the suggestion on a new chat", () => {
    setLastUsedProfileId("user-research");
    mockProfiles(profiles());
    renderPicker();
    expect(screen.getByText("研究")).toBeTruthy();
  });

  it("empty profile list with BYOK shows providers guide and keeps manage-combinations link", () => {
    mockProfiles(profiles({ data: [] }));
    mockProviders({ platform_available: false, billing_mode: "byok" });
    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: /模型组合：/ }));
    expect(screen.getByText("暂无可用组合")).toBeTruthy();
    const providersLink = screen.getByRole("link", { name: "接入服务商" });
    expect(providersLink.getAttribute("href")).toBe("/more/providers");
    expect(screen.getByText("管理组合…")).toBeTruthy();
  });

  it("empty profile list with platform_available shows retry/settings guide", () => {
    mockProfiles(profiles({ data: [] }));
    mockProviders({
      platform_available: true,
      billing_mode: "platform",
    });
    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: /模型组合：/ }));
    expect(screen.getByText("暂无可用组合")).toBeTruthy();
    expect(screen.getByText(/请稍后重试/)).toBeTruthy();
    const settingsLink = screen.getByRole("link", {
      name: "设置 · 模型",
    });
    expect(settingsLink.getAttribute("href")).toBe("/more/model");
    expect(screen.getByText("管理组合…")).toBeTruthy();
  });

  it("load failure is muted, not destructive", () => {
    useProfilesMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useLlmModelProfiles>);
    renderPicker();
    fireEvent.click(screen.getByRole("button", { name: /模型组合：/ }));
    const fail = screen.getByText("加载模型组合失败");
    expect(fail.className).toContain("text-muted-foreground");
    expect(fail.className).not.toContain("destructive");
  });
});
