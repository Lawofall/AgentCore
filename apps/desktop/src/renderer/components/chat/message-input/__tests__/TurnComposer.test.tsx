// @vitest-environment jsdom
/**
 * TurnComposer variants: `card` 摊开左簇；`bar` 用「＋」收纳会话配置，常显仅输入与发送。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useLlmProviders", () => ({
  useLlmProviders: () => ({
    data: {
      providers: [
        {
          id: "p1",
          label: "DeepSeek",
          base_url: "https://api.deepseek.com/v1",
          default_model: "deepseek-test",
          status: "active",
          supports_tools: true,
        },
      ],
      default_model_profile_id: "sys-52",
      billing_mode: "byok",
      platform_available: false,
      platform_model: null,
    },
    isLoading: false,
  }),
}));
vi.mock("@/hooks/useLlmModelProfiles", () => ({
  useLlmModelProfiles: () => ({
    data: {
      default_model_profile_id: "sys-52",
      data: [
        {
          id: "sys-52",
          name: "GLM-5.2",
          kind: "system",
          is_default: true,
          main: {
            origin: "byok",
            provider_id: "p1",
            model: "deepseek-test",
          },
          worker: null,
          background: null,
        },
      ],
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => [],
  getFolders: () => [],
  useCreateFolder: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => [],
  getConversations: () => [],
  useGroupedConversations: () => ({
    data: { folders: [], conversations: [] },
  }),
  patchConversationCache: vi.fn(),
}));
vi.mock("@/hooks/useModels", () => ({
  useModels: () => ({
    data: {
      byok_configured: true,
      current: { id: "deepseek-test", origin: "byok" },
      models: [
        {
          id: "deepseek-test",
          origin: "byok",
          display_name: "DeepSeek Test",
          vendor: "DeepSeek",
          capabilities: [],
          context_length: null,
          price: null,
          available: true,
        },
      ],
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: () => false,
  hasLocalEngine: () => false,
  // Desktop Electron under test — keep the web-only「无本地文件夹」chip off.
  isWebRuntime: () => false,
}));
vi.mock("@/services/permissionAxes", () => ({
  RECIPE_LABELS: {
    cautious: { short: "谨慎", description: "问" },
    less_interrupt: { short: "少打断", description: "少" },
    managed: { short: "托管", description: "同权" },
  },
  RECIPE_ORDER: ["cautious", "less_interrupt", "managed"],
  RECIPE_AXES: {
    less_interrupt: {
      file_write: "session",
      command: "auto",
      host: "session",
    },
  },
  DEFAULT_PERMISSION_AXES: {
    file_write: "session",
    command: "auto",
    host: "session",
  },
  FILE_WRITE_OPTIONS: [],
  COMMAND_OPTIONS: [],
  matchRecipe: () => "less_interrupt",
  axesShortLabel: () => "少打断",
  recipeToAxes: () => ({
    file_write: "session",
    command: "auto",
    host: "session",
  }),
  resolveDefaultPermissionAxes: () =>
    Promise.resolve({
      file_write: "session",
      command: "auto",
      host: "session",
    }),
  setConversationPermissionAxes: vi.fn(),
  setComposerDraftAxes: vi.fn(),
  confirmAutoCommandIfNeeded: () => true,
  isIllegalAxes: () => false,
}));
vi.mock("@/components/chat/message-input/useVoiceInput", () => ({
  useVoiceInput: () => ({
    isSupported: false,
    isRecording: false,
    interimText: "",
    duration: 0,
    state: "idle",
    toggle: vi.fn(),
    cancel: vi.fn(),
    stop: vi.fn(),
  }),
}));
const dropMock = vi.hoisted(() => ({
  handlePaste: vi.fn(),
  handleDrop: vi.fn(),
  dropError: null as string | null,
}));
vi.mock("@/components/chat/message-input/useComposerDrop", () => ({
  useComposerDrop: () => ({
    dragOver: false,
    dropError: dropMock.dropError,
    clearDropError: vi.fn(),
    attachDroppedFile: vi.fn(),
    attachFiles: vi.fn(),
    handleDragOver: vi.fn(),
    handleDragLeave: vi.fn(),
    handleDrop: dropMock.handleDrop,
    handlePaste: dropMock.handlePaste,
  }),
}));

// isGenerating 来自 activeRuntime().isGenerating（非顶层字段），构造完整 runtime 太脆，
// 直接 mock 这个 hook；其余 store 行为（setState / getState）保留真实实现。
const genMock = vi.hoisted(() => ({ value: false }));
const handleSendMock = vi.hoisted(() => vi.fn());
const sendingMock = vi.hoisted(() => ({ value: false }));
const mentionToggleMock = vi.hoisted(() => vi.fn());

vi.mock("@/components/chat/message-input/useComposerSend", () => ({
  useComposerSend: () => ({
    handleSend: handleSendMock,
    isSending: sendingMock.value,
  }),
}));
vi.mock("@/components/chat/message-input/useMentionMenu", () => ({
  useMentionMenu: () => ({
    menuMode: null,
    sections: [],
    flatItems: [],
    items: [],
    activeIndex: 0,
    indexLoading: false,
    menuError: null,
    query: "",
    sourceCount: 0,
    indexLoadedRef: { current: true },
    searchInputRef: { current: null },
    showCategoryLevel: false,
    categories: [],
    canGoBack: false,
    closeMenu: vi.fn(),
    syncMention: vi.fn(),
    handleMenuNavKey: () => false,
    attachEntry: vi.fn(),
    selectItem: vi.fn(),
    setActiveIndex: vi.fn(),
    setQuery: vi.fn(),
    handleAddRoot: vi.fn(),
    pickLocalFile: vi.fn(),
    toggleAtMention: mentionToggleMock,
    clearActiveMention: vi.fn(),
    drillCategory: vi.fn(),
    goBack: vi.fn(),
  }),
}));

vi.mock("@/stores/conversation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/conversation")>();
  return { ...actual, useActiveGenerating: () => genMock.value };
});

import {
  COMPOSER_CONTINUE_PLACEHOLDER,
  COMPOSER_EMPTY_INTERRUPTED_HINT,
} from "@/lib/composerContinueHint";
import { useComposerProfileDraftStore } from "@/lib/composerModelProfile";
import { LLM_RATE_LIMIT_MESSAGE, LLM_RATE_LIMIT_WHY } from "@/lib/errors";
import {
  setComposerSendError,
  useComposerSendErrorStore,
} from "@/stores/composerSendError";
import {
  type Message,
  getActiveRuntime,
  useConversationStore,
} from "@/stores/conversation";
import { DRAFT_KEY, EMPTY_RUNTIME } from "@/stores/conversation/runtime";
import { type ExecutionPlan, useExecutionStore } from "@/stores/execution";
import { useServerHealthStore } from "@/stores/serverHealth";
import { TurnComposer } from "../TurnComposer";
import { COMPOSER_DEBATE_STEER_PLACEHOLDER } from "../liveDebateSteer";

const OUTCOME_CID = "conv-composer-outcome";
const DEBATE_MID = "a-debate-1";

const LIVE_DEBATE_PLAN: ExecutionPlan = {
  id: "exec-d",
  planType: "debate",
  taskSummary: "该不该上",
  agents: [
    { id: "a-pro", role: "正方" },
    { id: "a-con", role: "反方" },
  ],
  runs: [
    {
      id: "r-pro",
      agentId: "a-pro",
      task: "立论",
      dependsOn: [],
      stance: "pro",
      group: "debate:debate",
      round: 1,
    },
    {
      id: "r-con",
      agentId: "a-con",
      task: "反驳",
      dependsOn: [],
      stance: "con",
      group: "debate:debate",
      round: 1,
    },
  ],
};

function seedLastAssistant(
  partial: Partial<Message> & Pick<Message, "finishReason">,
  sessionError: string | null = null,
) {
  const message: Message = {
    id: "a1",
    role: "assistant",
    content: "",
    createdAt: new Date().toISOString(),
    executionId: "exec-1",
    isStreaming: false,
    traceId: "trace-1",
    ...partial,
  };
  useConversationStore.setState({
    currentConversationId: OUTCOME_CID,
    byId: {
      [OUTCOME_CID]: {
        ...EMPTY_RUNTIME,
        error: sessionError,
        messages: [message],
      },
    },
  });
}

function seedLiveDebate() {
  useConversationStore.setState({
    currentConversationId: OUTCOME_CID,
    byId: {
      [OUTCOME_CID]: {
        ...EMPTY_RUNTIME,
        isGenerating: true,
        messages: [
          {
            id: DEBATE_MID,
            role: "assistant",
            content: "",
            createdAt: new Date().toISOString(),
            executionId: "exec-d",
            isStreaming: true,
          },
        ],
      },
    },
  });
  useExecutionStore.getState().startExecution(LIVE_DEBATE_PLAN, DEBATE_MID);
}

function renderComposer(variant?: "card" | "bar") {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <TurnComposer variant={variant} />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

beforeEach(async () => {
  genMock.value = false;
  sendingMock.value = false;
  handleSendMock.mockClear();
  mentionToggleMock.mockClear();
  dropMock.handlePaste.mockClear();
  dropMock.handleDrop.mockClear();
  dropMock.dropError = null;
  useConversationStore.setState({
    currentConversationId: null,
    byId: {},
  } as never);
  useExecutionStore.setState({ byId: {} });
  useComposerSendErrorStore.setState({ byKey: {} });
  useServerHealthStore.setState({
    status: "online",
    reason: null,
    justRecovered: false,
  });
  const { useComposerDraftStore } = await import("@/stores/composer");
  useComposerDraftStore.setState({
    drafts: {},
    fillToken: 0,
    dockFlipToken: 0,
  });
  useComposerProfileDraftStore.setState({ profileId: null });
});

afterEach(cleanup);

function expectWorkspaceBeforeModel(root: ParentNode = document) {
  const workspace = screen.getByLabelText("在哪工作");
  const model = screen.getByLabelText(/模型组合：/);
  const nodes = root.querySelectorAll("button, [aria-label]");
  const order = [...nodes];
  expect(order.indexOf(workspace)).toBeGreaterThanOrEqual(0);
  expect(order.indexOf(model)).toBeGreaterThan(order.indexOf(workspace));
}

describe("TurnComposer variants", () => {
  it("defaults to card: workspace then model in left cluster, no「更多」", () => {
    const { container } = renderComposer();
    expect(
      container.querySelector('[data-composer-variant="card"]'),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "更多选项" })).toBeNull();
    expect(screen.getByLabelText("在哪工作")).toBeTruthy();
    expect(screen.getByLabelText(/模型组合：/)).toBeTruthy();
    expect(screen.getByLabelText(/权限：/)).toBeTruthy();
    expect(screen.getByLabelText("@ 引用")).toBeTruthy();
    expectWorkspaceBeforeModel(container);
  });

  it("bar: 「更多选项」收纳左簇；未打开时不占常显", () => {
    renderComposer("bar");
    expect(
      document.querySelector('[data-composer-variant="bar"]'),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "更多选项" })).toBeTruthy();
    // 收纳前：工作区 / 模型 / 附件不在常显条上
    expect(screen.queryByLabelText("在哪工作")).toBeNull();
    expect(screen.queryByLabelText(/模型组合：/)).toBeNull();
    expect(screen.queryByLabelText("@ 引用")).toBeNull();
    expect(screen.getByRole("button", { name: "发送" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "更多选项" }));
    const menu = screen.getByTestId("composer-plus-menu");
    expect(menu).toBeTruthy();
    expect(screen.getByLabelText("在哪工作")).toBeTruthy();
    expect(screen.getByLabelText(/模型组合：/)).toBeTruthy();
    expect(screen.getByLabelText(/权限：/)).toBeTruthy();
    expect(screen.getByLabelText("@ 引用")).toBeTruthy();
    expect(within(menu).getByText("引用")).toBeTruthy();
    expect(menu.className).not.toMatch(/\bw-72\b/);
    expectWorkspaceBeforeModel(menu);
    expect(within(menu).queryByText("后台云端")).toBeNull();
    expect(within(menu).queryByRole("button", { name: /后台云端/ })).toBeNull();
  });

  it("bar: 模型在＋内展开，不另开一层；返回回到列表", () => {
    renderComposer("bar");
    fireEvent.click(screen.getByRole("button", { name: "更多选项" }));
    const menu = screen.getByTestId("composer-plus-menu");
    fireEvent.click(screen.getByLabelText(/模型组合：/));
    expect(menu.getAttribute("data-plus-panel")).toBe("model");
    expect(within(menu).getByRole("button", { name: "返回" })).toBeTruthy();
    expect(within(menu).getByText("管理组合…")).toBeTruthy();
    expect(screen.queryByLabelText("在哪工作")).toBeNull();
    fireEvent.click(within(menu).getByRole("button", { name: "返回" }));
    expect(menu.getAttribute("data-plus-panel")).toBe("list");
    expect(screen.getByLabelText("在哪工作")).toBeTruthy();
  });

  it("bar: 工作区在＋内展开", () => {
    renderComposer("bar");
    fireEvent.click(screen.getByRole("button", { name: "更多选项" }));
    const menu = screen.getByTestId("composer-plus-menu");
    fireEvent.click(screen.getByLabelText("在哪工作"));
    expect(menu.getAttribute("data-plus-panel")).toBe("workspace");
    expect(within(menu).getByRole("button", { name: "快速对话" })).toBeTruthy();
    expect(within(menu).getByRole("button", { name: "返回" })).toBeTruthy();
  });

  it("N4-A: offline hard-disables 发送 even with draft text", async () => {
    useServerHealthStore.setState({
      status: "offline",
      reason: "unreachable",
      justRecovered: false,
    });
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "hello offline");
    renderComposer("bar");
    const send = screen.getByRole("button", { name: "发送" });
    expect((send as HTMLButtonElement).disabled).toBe(true);
    const offline = screen.getByText(/可浏览已缓存的对话与本机文件（只读）/);
    expect(offline.className).toContain("text-muted-foreground");
    expect(offline.className).not.toContain("destructive");
  });

  it("drop failure row is muted, not destructive", () => {
    dropMock.dropError = "上传附件到云端工作区失败";
    renderComposer();
    const row = screen.getByText("上传附件到云端工作区失败");
    expect(row.closest("output")?.className).toContain("text-muted-foreground");
    expect(row.closest("output")?.className).not.toContain("destructive");
  });

  it("generating + empty: bar shows only 停止生成 (no mid-flight send)", () => {
    genMock.value = true;
    renderComposer("bar");
    expect(screen.queryByRole("button", { name: "插入" })).toBeNull();
    expect(screen.queryByRole("button", { name: "排队发送" })).toBeNull();
    expect(screen.queryByRole("button", { name: "插队" })).toBeNull();
    expect(screen.getByRole("button", { name: "停止生成" })).toBeTruthy();
  });

  it("stopping: stop button shows 停止中… spinner and stays clickable", () => {
    genMock.value = true;
    useConversationStore.setState({
      byId: {
        [DRAFT_KEY]: { ...EMPTY_RUNTIME, turnPhase: "stopping" },
      },
    });
    renderComposer("bar");
    const stop = screen.getByRole("button", { name: "停止中…" });
    expect((stop as HTMLButtonElement).disabled).toBe(false);
    expect(stop.getAttribute("aria-busy")).toBe("true");
    expect(stop.querySelector(".animate-spin")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "停止生成" })).toBeNull();
  });

  it("stopping + draft: 排队/插队 remain; stop button is 停止中…", async () => {
    genMock.value = true;
    useConversationStore.setState({
      byId: {
        [DRAFT_KEY]: { ...EMPTY_RUNTIME, turnPhase: "stopping" },
      },
    });
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "下一句");
    renderComposer("bar");
    expect(screen.getByRole("button", { name: "排队发送" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "插队" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "停止中…" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "停止生成" })).toBeNull();
  });

  it("stop HTTP rollback: stopping UI returns to 停止生成", () => {
    genMock.value = true;
    useConversationStore.setState({
      byId: {
        [DRAFT_KEY]: { ...EMPTY_RUNTIME, turnPhase: "stopping" },
      },
    });
    renderComposer("bar");
    expect(screen.getByRole("button", { name: "停止中…" })).toBeTruthy();

    act(() => {
      useConversationStore.getState().setTurnPhase("streaming");
    });
    expect(screen.getByRole("button", { name: "停止生成" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "停止中…" })).toBeNull();
    const stop = screen.getByRole("button", { name: "停止生成" });
    expect(stop.getAttribute("aria-busy")).toBeNull();
    expect(stop.querySelector(".animate-spin")).toBeNull();
  });

  it("generating + draft: 排队发送 + 插队 + 停止生成 coexist", async () => {
    genMock.value = true;
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "下一句");
    renderComposer("bar");
    expect(screen.getByRole("button", { name: "排队发送" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "插队" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "停止生成" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "插入" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "插队" }));
    expect(handleSendMock).toHaveBeenCalledWith({ delivery: "steer" });

    handleSendMock.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "排队发送" }));
    expect(handleSendMock).toHaveBeenCalledWith();
  });

  it("生成中：@ 可点、粘贴与拖入照旧进附件收集", () => {
    // 插话 / 排队本就带附件走（useComposerSend 的 mid-flight 分支会 settleAttachments），
    // 禁用 @ 既不一致、又零反馈：用户只会以为入口坏了。
    genMock.value = true;
    mentionToggleMock.mockClear();
    const { container } = renderComposer();

    const mentionBtn = screen.getByLabelText("@ 引用") as HTMLButtonElement;
    expect(mentionBtn.disabled).toBe(false);
    fireEvent.click(mentionBtn);
    expect(mentionToggleMock).toHaveBeenCalled();

    const body = screen.getByTestId("composer-body");
    const root = container.querySelector("[data-composer-variant]");
    if (!root) throw new Error("composer root missing");

    fireEvent.paste(body);
    expect(dropMock.handlePaste).toHaveBeenCalled();
    fireEvent.drop(root);
    expect(dropMock.handleDrop).toHaveBeenCalled();
  });

  it("generating + draft: centered card also shows 排队发送 + 插队 + 停止", async () => {
    genMock.value = true;
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "下一句");
    renderComposer();
    expect(screen.getByRole("button", { name: "排队发送" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "插队" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "停止生成" })).toBeTruthy();
  });

  it("generating + draft: Ctrl/Cmd+Enter forces steer", async () => {
    genMock.value = true;
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "插一句");
    renderComposer("bar");
    const body = screen.getByTestId("composer-body");
    fireEvent.keyDown(body, { key: "Enter", ctrlKey: true });
    expect(handleSendMock).toHaveBeenCalledWith({ delivery: "steer" });
  });

  it("idle: Ctrl/Cmd+Enter matches Enter (no fake queue)", async () => {
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "hello");
    renderComposer("bar");
    const body = screen.getByTestId("composer-body");
    fireEvent.keyDown(body, { key: "Enter", metaKey: true });
    expect(handleSendMock).toHaveBeenCalledWith();
  });

  it("live debate + generating: hides 排队/插队, shows 出结论", async () => {
    genMock.value = true;
    seedLiveDebate();
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue(OUTCOME_CID, "再问定价");
    renderComposer("bar");
    expect(screen.queryByRole("button", { name: "排队发送" })).toBeNull();
    expect(screen.queryByRole("button", { name: "插队" })).toBeNull();
    expect(screen.getByRole("button", { name: "发送" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "出结论" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "停止生成" })).toBeTruthy();
    expect(
      screen.getByRole("textbox", { name: COMPOSER_DEBATE_STEER_PLACEHOLDER }),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "出结论" }));
    expect(handleSendMock).toHaveBeenCalledWith({ debateSteer: "conclude" });

    handleSendMock.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(handleSendMock).toHaveBeenCalledWith();
  });

  it("live debate + generating: Enter and Ctrl/Cmd+Enter both continue (not queue/steer)", async () => {
    genMock.value = true;
    seedLiveDebate();
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue(OUTCOME_CID, "再问定价");
    renderComposer("bar");
    const body = screen.getByTestId("composer-body");
    fireEvent.keyDown(body, { key: "Enter" });
    expect(handleSendMock).toHaveBeenCalledWith();
    handleSendMock.mockClear();
    fireEvent.keyDown(body, { key: "Enter", ctrlKey: true });
    expect(handleSendMock).toHaveBeenCalledWith();
    expect(handleSendMock).not.toHaveBeenCalledWith({ delivery: "steer" });
  });

  it("live debate + empty draft: 出结论 still available, no 排队/插队", () => {
    genMock.value = true;
    seedLiveDebate();
    renderComposer("bar");
    expect(screen.queryByRole("button", { name: "排队发送" })).toBeNull();
    expect(screen.queryByRole("button", { name: "插队" })).toBeNull();
    expect(screen.queryByRole("button", { name: "发送" })).toBeNull();
    expect(screen.getByRole("button", { name: "出结论" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "停止生成" })).toBeTruthy();
  });

  it("live debate: hides @ 入口 (card 常显 and bar ＋菜单)", () => {
    genMock.value = true;
    seedLiveDebate();
    const { unmount } = renderComposer();
    expect(screen.queryByLabelText("@ 引用")).toBeNull();
    unmount();

    renderComposer("bar");
    expect(screen.queryByLabelText("@ 引用")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "更多选项" }));
    expect(screen.queryByLabelText("@ 引用")).toBeNull();
    expect(screen.queryByText("引用")).toBeNull();
  });

  it("live debate: mention 芯片不点亮发送", async () => {
    genMock.value = true;
    seedLiveDebate();
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore
      .getState()
      .setAgentMentions(OUTCOME_CID, [
        { id: "m-1", agentId: "a-1", role: "研究员" },
      ]);
    renderComposer("bar");
    expect(screen.queryByRole("button", { name: "发送" })).toBeNull();
    expect(screen.getByRole("button", { name: "出结论" })).toBeTruthy();
  });

  it("idle: single 发送, no mid-flight / 停止", () => {
    renderComposer("bar");
    expect(screen.queryByRole("button", { name: "插入" })).toBeNull();
    expect(screen.queryByRole("button", { name: "排队发送" })).toBeNull();
    expect(screen.queryByRole("button", { name: "插队" })).toBeNull();
    expect(screen.queryByRole("button", { name: "停止生成" })).toBeNull();
    expect(screen.getByRole("button", { name: "发送" })).toBeTruthy();
  });

  it("idle: attachment-only draft enables 发送 (empty text)", async () => {
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "");
    useComposerDraftStore.getState().setAttachments("__draft__", [
      {
        id: "a1",
        key: "file:local:pic.png",
        name: "pic.png",
        path: "pic.png",
        text: "",
        truncated: false,
        kind: "file",
        binary: true,
        workspacePath: "attachments/pic.png",
      },
    ]);
    renderComposer("bar");
    expect(screen.getByTestId("composer-vision-hint")).toBeTruthy();
    const send = screen.getByRole("button", { name: "发送" });
    expect((send as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(send);
    expect(handleSendMock).toHaveBeenCalledWith();
  });

  it("idle: empty text and no attachments keeps 发送 disabled", () => {
    renderComposer("bar");
    const send = screen.getByRole("button", { name: "发送" });
    expect((send as HTMLButtonElement).disabled).toBe(true);
  });

  it("has no composer textarea; body editor is the textbox", () => {
    const { container } = renderComposer();
    expect(container.querySelector("textarea")).toBeNull();
    expect(screen.getByTestId("composer-body")).toBeTruthy();
    expect(screen.getByRole("textbox")).toBeTruthy();
  });

  it("migrates a legacy chip-tray draft into inline markers", async () => {
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "hello");
    useComposerDraftStore.getState().setAttachments("__draft__", [
      {
        id: "a1",
        key: "file:local:pic.png",
        name: "pic.png",
        path: "pic.png",
        text: "",
        truncated: false,
        kind: "file",
      },
    ]);
    renderComposer();
    expect(useComposerDraftStore.getState().drafts.__draft__?.value).toContain(
      "\uFFFC",
    );
  });

  it("file pill shows the filename without a 文件 kind label", async () => {
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "");
    useComposerDraftStore.getState().setAttachments("__draft__", [
      {
        id: "a1",
        key: "file:local:现行信息.md",
        name: "现行信息.md",
        path: "docs/01-产品/现行信息.md",
        text: "",
        truncated: false,
        kind: "file",
      },
    ]);
    renderComposer("bar");
    const body = screen.getByTestId("composer-body");
    await waitFor(() => {
      expect(body.textContent).toContain("现行信息.md");
    });
    expect(body.textContent).not.toContain("文件");
  });

  it("empty draft: composer send error is on the card, not in the body", () => {
    const copy = "发送失败：没有可用的模型密钥";
    setComposerSendError("__draft__", { message: copy, action: null });
    renderComposer();
    expect(screen.getByTestId("composer-send-error").textContent).toContain(
      copy,
    );
    const body = screen.getByTestId("composer-body");
    expect(body.textContent ?? "").not.toContain(copy);
  });

  it("empty draft: falls back to session error when composer slot is empty", () => {
    const copy = "重新生成失败，请稍后重试";
    useConversationStore.getState().setError(copy, null);
    renderComposer();
    expect(screen.getByTestId("composer-send-error").textContent).toContain(
      copy,
    );
    const body = screen.getByTestId("composer-body");
    expect(body.textContent ?? "").not.toContain(copy);
  });

  it("closing the send-error notice clears composer and session slots", () => {
    const copy = "发送失败，请稍后重试";
    setComposerSendError("__draft__", { message: copy, action: null });
    useConversationStore.getState().setError(copy, null);
    renderComposer();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByTestId("composer-send-error")).toBeNull();
    expect(
      useComposerSendErrorStore.getState().byKey.__draft__,
    ).toBeUndefined();
    expect(getActiveRuntime().error).toBeNull();
  });

  it("empty interrupt: composer hint hosts 复制排查包", () => {
    seedLastAssistant({ finishReason: "interrupted", content: "" });
    renderComposer();
    const hint = screen.getByTestId("composer-empty-interrupted-hint");
    expect(hint.textContent).toContain("发送下一条");
    expect(hint.textContent).toContain(COMPOSER_EMPTY_INTERRUPTED_HINT);
    expect(
      within(hint).getByRole("button", { name: "复制排查包" }),
    ).toBeTruthy();
    expect(screen.queryByTestId("composer-send-error")).toBeNull();
  });

  it("partial + rate-limit: composer hint hosts why and 复制排查包", () => {
    seedLastAssistant({
      finishReason: "error",
      content: "",
      outcome: "partial",
      error: {
        code: "LLM_RATE_LIMIT",
        message: LLM_RATE_LIMIT_MESSAGE,
      },
    });
    renderComposer();
    const hint = screen.getByTestId("composer-empty-interrupted-hint");
    expect(hint.textContent).toContain(LLM_RATE_LIMIT_WHY);
    expect(hint.textContent).not.toContain("发送下一条");
    expect(hint.textContent).not.toContain("未能交付");
    expect(
      within(hint).getByRole("button", { name: "复制排查包" }),
    ).toBeTruthy();
    expect(screen.queryByTestId("composer-send-error")).toBeNull();
  });

  it("empty interrupt: sessionError is suppressed (hint is the unique verdict)", () => {
    seedLastAssistant(
      { finishReason: "interrupted", content: "" },
      "网络中断，请重试。",
    );
    renderComposer();
    expect(screen.getByTestId("composer-empty-interrupted-hint")).toBeTruthy();
    expect(
      within(screen.getByTestId("composer-empty-interrupted-hint")).getByRole(
        "button",
        { name: "复制排查包" },
      ),
    ).toBeTruthy();
    expect(screen.queryByTestId("composer-send-error")).toBeNull();
  });

  it("empty user-stop is not an error: no composer hint", () => {
    seedLastAssistant({ finishReason: "cancelled", content: "" });
    renderComposer();
    expect(screen.queryByTestId("composer-empty-interrupted-hint")).toBeNull();
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
  });

  it("team-graph user-stop does not paint 已停止 as a composer warning banner", () => {
    seedLastAssistant(
      {
        finishReason: "cancelled",
        content: "半成品导语",
        process: [{ kind: "team", execution_id: "exec-1" }],
      },
      "已停止",
    );
    renderComposer();
    expect(screen.queryByTestId("composer-send-error")).toBeNull();
    expect(screen.queryByTestId("composer-empty-interrupted-hint")).toBeNull();
  });

  it("session banner lights when the arbitrator has no other verdict", () => {
    seedLastAssistant(
      { finishReason: "end_turn", content: "已写出正文" },
      "网络中断，请重试。",
    );
    renderComposer();
    const banner = screen.getByTestId("composer-send-error");
    expect(banner.textContent).toContain("网络中断，请重试。");
    expect(screen.getByRole("button", { name: "复制排查包" })).toBeTruthy();
    expect(screen.queryByTestId("composer-empty-interrupted-hint")).toBeNull();
  });

  it("bubble-owned failure does not duplicate sessionError on the composer", () => {
    seedLastAssistant(
      {
        finishReason: "error",
        content: "",
        error: { code: "LLM_ERROR", message: "模型调用失败，请重试。" },
      },
      "模型调用失败，请重试。",
    );
    renderComposer();
    expect(screen.queryByTestId("composer-send-error")).toBeNull();
    expect(screen.queryByTestId("composer-empty-interrupted-hint")).toBeNull();
  });

  it("composerError still shows when the turn would suppress sessionError", () => {
    seedLastAssistant(
      {
        finishReason: "error",
        content: "",
        error: { code: "LLM_ERROR", message: "模型调用失败，请重试。" },
      },
      "模型调用失败，请重试。",
    );
    setComposerSendError(OUTCOME_CID, {
      message: "发送失败：没有可用的模型密钥",
      action: null,
    });
    renderComposer();
    expect(screen.getByTestId("composer-send-error").textContent).toContain(
      "发送失败：没有可用的模型密钥",
    );
    expect(screen.queryByTestId("composer-empty-interrupted-hint")).toBeNull();
  });

  it("composerError with supportPack hosts 复制排查包 after bubbles are gone", () => {
    setComposerSendError("__draft__", {
      message: "上游限流，暂时无法继续本回合。",
      action: null,
      supportPack: {
        conversationId: "c1",
        userMessageId: "u1",
        messageId: "a1",
        errorCode: "LLM_RATE_LIMIT",
      },
    });
    renderComposer();
    expect(screen.getByTestId("composer-send-error").textContent).toContain(
      "上游限流",
    );
    expect(screen.getByRole("button", { name: "复制排查包" })).toBeTruthy();
  });

  it("interrupted-with-body: continue placeholder, no empty-interrupt hint", () => {
    seedLastAssistant({ finishReason: "interrupted", content: "半成品" });
    renderComposer();
    expect(
      screen.getByRole("textbox", { name: COMPOSER_CONTINUE_PLACEHOLDER }),
    ).toBeTruthy();
    expect(screen.queryByTestId("composer-empty-interrupted-hint")).toBeNull();
  });

  it("发送中：按钮进入 in-flight 态并挡住连点", async () => {
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "带附件的一条");
    sendingMock.value = true;
    renderComposer("bar");

    const send = screen.getByRole("button", { name: "发送" });
    expect(send.getAttribute("data-sending")).toBe("true");
    expect(send.getAttribute("aria-busy")).toBe("true");
    expect((send as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(send);
    expect(handleSendMock).not.toHaveBeenCalled();
  });
});
