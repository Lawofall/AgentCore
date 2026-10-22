// @vitest-environment jsdom
/**
 * TurnComposer：空草稿卡片上方常显工作区 · 权限 · 能力 · 统筹，＋打开引用目录。
 * 有会话后世界行与＋收进「更多」。`bar` 输入与按钮同一行。
 * 收到的上下文贴在整块输入框外侧右边。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import { NarrowLayoutProvider } from "@/lib/narrowLayout";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
          vendor_id: "deepseek",
          status: "active",
          supports_tools: true,
        },
      ],
      default_assembly_id: "sys-52",
      billing_mode: "byok",
      platform_available: false,
      platform_model: null,
    },
    isLoading: false,
  }),
}));
const profileRows = vi.hoisted(() => ({
  data: [
    {
      id: "sys-52",
      name: "GLM-5.2",
      kind: "system",
      recipe: "full",
      is_default: true,
      main: {
        origin: "byok",
        provider_id: "p1",
        model: "deepseek-test",
      },
      worker: null,
    },
  ] as Array<Record<string, unknown>>,
}));
const PROFILE_FULL = profileRows.data[0];
const PROFILE_CHAT = {
  ...PROFILE_FULL,
  id: "sys-chat",
  name: "极简",
  recipe: "chat",
  is_default: false,
};
vi.mock("@/hooks/useLlmModelProfiles", () => ({
  useLlmModelProfiles: () => ({
    data: {
      default_assembly_id: "sys-52",
      data: profileRows.data,
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));
const folderLists = vi.hoisted(() => ({
  accessible: [] as {
    id: string;
    name: string;
    mode: "local" | "cloud";
    localRootId: string | null;
    localSubpath: string | null;
    myRole?: "owner" | "editor" | "viewer" | null;
  }[],
  grouped: [] as {
    id: string;
    name: string;
    mode: "local" | "cloud";
    localRootId: string | null;
    localSubpath: string | null;
    myRole?: "owner" | "editor" | "viewer" | null;
  }[],
  conversations: [] as {
    id: string;
    title: string;
    updatedAt: string;
    messageCount: number;
    lastMessagePreview: string | null;
    folderId?: string | null;
    assemblyId?: string | null;
  }[],
}));

vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => folderLists.accessible,
  getFolders: () => folderLists.accessible,
  useCreateFolder: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => folderLists.conversations,
  getConversations: () => folderLists.conversations,
  useGroupedConversations: () => ({
    data: {
      folders: folderLists.grouped,
      conversations: folderLists.conversations,
    },
  }),
  patchConversationCache: vi.fn(),
}));
vi.mock("@/hooks/useFolderSharing", () => ({
  useSharedWithMeFolders: () => ({ data: [] }),
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
const capState = vi.hoisted(() => ({ native: false }));
vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: () => false,
  hasLocalEngine: () => false,
  // Desktop Electron under test — keep the web-only「无本地文件夹」chip off.
  isWebRuntime: () => false,
  hasNativeMediaCapture: () => capState.native,
  isNativeRuntime: () => capState.native,
}));
vi.mock("@/hooks/useWorkspaces", () => ({
  useConversationWorkspace: () => null,
  useWorkspaces: () => ({ data: [] }),
}));
vi.mock("@/hooks/useExternalGrants", () => ({
  useExternalGrants: () => ({ data: [], isError: false, refetch: vi.fn() }),
  useRevokeExternalGrant: () => ({ mutate: vi.fn(), isPending: false }),
}));
const toolBoard = vi.hoisted(() => ({
  disabled: [] as string[],
  switches: [] as [],
}));

vi.mock("@/services/toolSwitches", () => ({
  subscribeAccountToolSwitches: () => () => {},
  getAccountToolSwitchboard: () => toolBoard,
  loadAccountToolSwitches: vi.fn(async () => toolBoard),
  subscribeComposerDraftDisabledTools: () => () => {},
  getComposerDraftDisabledTools: () => null,
  setComposerDraftDisabledTools: vi.fn(),
  peekComposerDraftDisabledTools: () => null,
  saveAccountToolSwitches: vi.fn(),
  saveConversationToolSwitches: vi.fn(),
  loadConversationToolSwitches: vi.fn(async () => toolBoard),
  disabledAfterToggle: (disabled: string[], id: string, on: boolean) =>
    on ? disabled.filter((item) => item !== id) : [...disabled, id],
}));
vi.mock("@/services/permissionAxes", () => ({
  BOUNDARY_LABELS: {
    read: { short: "只看", description: "看" },
    folder: { short: "本文件夹", description: "文件夹" },
    computer: { short: "这台电脑", description: "电脑" },
    full: { short: "完全权限", description: "全权" },
  },
  BOUNDARY_ORDER: ["read", "folder", "computer", "full"],
  DEFAULT_PERMISSION_AXES: { boundary: "computer" },
  factoryPermissionAxes: () => ({ boundary: "folder" }),
  boundaryShortLabel: () => "本文件夹",
  axesEqual: () => false,
  resolveDefaultPermissionAxes: () => Promise.resolve({ boundary: "folder" }),
  setConversationPermissionAxes: vi.fn(),
  setComposerDraftAxes: vi.fn(),
  rememberBoundarySelection: vi.fn(),
  confirmComputerIfNeeded: () => true,
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
const openBrowseMock = vi.hoisted(() => vi.fn());

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
    openBrowse: openBrowseMock,
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
import { setComposerDraftDelegateMode } from "@/lib/composerDelegateMode";
import { useComposerProfileDraftStore } from "@/lib/composerModelProfile";
import { LLM_RATE_LIMIT_MESSAGE, LLM_RATE_LIMIT_WHY } from "@/lib/errors";
import type { FolderMeta } from "@/services/folders";
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
import { useFoldersStore } from "@/stores/folders";
import { useServerHealthStore } from "@/stores/serverHealth";
import { useUIStore } from "@/stores/ui";
import { TurnComposer } from "../TurnComposer";
import { COMPOSER_FOLDER_READ_ONLY_HINT } from "../composerFolderWrite";
import { COMPOSER_DEBATE_STEER_PLACEHOLDER } from "../liveDebateSteer";

const OUTCOME_CID = "conv-composer-outcome";
const DEBATE_MID = "a-debate-1";
const COORD_MID = "a-coord-1";

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

const TEAM_PLAN: ExecutionPlan = {
  id: "exec-team",
  planType: "multi_agent",
  taskSummary: "调研",
  agents: [{ id: "a-w", role: "研究员" }],
  runs: [
    {
      id: "r-w",
      agentId: "a-w",
      task: "查资料",
      dependsOn: [],
    },
  ],
};

function seedCoordinationActive() {
  useConversationStore.setState({
    currentConversationId: OUTCOME_CID,
    byId: {
      [OUTCOME_CID]: {
        ...EMPTY_RUNTIME,
        isGenerating: true,
        messages: [
          {
            id: COORD_MID,
            role: "assistant",
            content: "",
            createdAt: new Date().toISOString(),
            executionId: "exec-team",
            isStreaming: true,
          },
        ],
      },
    },
  });
  useExecutionStore.getState().startExecution(TEAM_PLAN, COORD_MID);
}

function seedCoordinationLampOff() {
  useConversationStore.setState({
    currentConversationId: OUTCOME_CID,
    byId: {
      [OUTCOME_CID]: {
        ...EMPTY_RUNTIME,
        isGenerating: false,
        turnPhase: "idle",
        messages: [
          {
            id: COORD_MID,
            role: "assistant",
            content: "换打法",
            createdAt: new Date().toISOString(),
            executionId: "exec-team",
            isStreaming: false,
          },
        ],
      },
    },
  });
  useExecutionStore.getState().startExecution(TEAM_PLAN, COORD_MID);
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
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TooltipProvider>
          <TurnComposer variant={variant} />
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const DEFAULT_PLACEHOLDER = "输入消息，@ 引用内容…";

function renderNarrowComposer(variant?: "card" | "bar") {
  const previous = window.matchMedia;
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: String(query).includes("max-width"),
    media: String(query),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <NarrowLayoutProvider>
          <TooltipProvider>
            <TurnComposer variant={variant} />
          </TooltipProvider>
        </NarrowLayoutProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return {
    ...view,
    restore() {
      window.matchMedia = previous;
    },
  };
}

function cloudDesk(
  id: string,
  name: string,
  myRole: "owner" | "editor" | "viewer",
): FolderMeta {
  return {
    id,
    name,
    mode: "cloud",
    localRootId: null,
    localSubpath: null,
    myRole,
  };
}

function listedConv(id: string, folderId: string | null) {
  return {
    id,
    title: "对话",
    updatedAt: "2026-01-01T00:00:00Z",
    messageCount: 1,
    lastMessagePreview: null,
    folderId,
  };
}

function seedOpenConversation(id: string) {
  useConversationStore.setState({
    currentConversationId: id,
    byId: { [id]: { ...EMPTY_RUNTIME } },
  });
}

beforeEach(async () => {
  capState.native = false;
  genMock.value = false;
  sendingMock.value = false;
  handleSendMock.mockClear();
  mentionToggleMock.mockClear();
  openBrowseMock.mockClear();
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
  setComposerDraftDelegateMode(false);
  useComposerProfileDraftStore.setState({
    profileId: null,
    assemblyId: null,
    modelOverride: null,
  });
  useFoldersStore.setState({ draftWorkspaceIntent: { kind: "quick_cloud" } });
  useUIStore.setState({ sendOnEnter: true });
  folderLists.accessible = [];
  folderLists.grouped = [];
  folderLists.conversations = [];
  profileRows.data = [{ ...PROFILE_FULL }];
});

afterEach(cleanup);

function expectWorldThenSend(root: ParentNode = document) {
  const workspace = screen.getByLabelText("在哪工作");
  const assembly = screen.getByLabelText(/能力：/);
  const delegate = screen.getByRole("switch", { name: "统筹" });
  const permission = screen.getByLabelText(/权限：/);
  const model = screen.getByLabelText(/模型：/);
  const order = [...root.querySelectorAll("button, [aria-label]")];
  expect(order.indexOf(workspace)).toBeGreaterThanOrEqual(0);
  expect(order.indexOf(permission)).toBeGreaterThan(order.indexOf(workspace));
  expect(order.indexOf(assembly)).toBeGreaterThan(order.indexOf(permission));
  expect(order.indexOf(delegate)).toBeGreaterThan(order.indexOf(assembly));
  expect(order.indexOf(model)).toBeGreaterThan(order.indexOf(delegate));
}

describe("TurnComposer variants", () => {
  it("defaults to card: world row outside the card, model in send cluster", () => {
    const { container } = renderComposer();
    expect(
      container.querySelector('[data-composer-variant="card"]'),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "更多" })).toBeNull();
    expect(screen.getByRole("button", { name: "添加" })).toBeTruthy();
    expect(screen.getByLabelText("在哪工作")).toBeTruthy();
    expect(screen.getByLabelText(/模型：/)).toBeTruthy();
    const modelChip = screen.getByLabelText(/模型：/);
    expect(modelChip.className).toContain("text-foreground");
    expect(modelChip.className).not.toContain("text-muted-foreground");
    expect(modelChip.querySelector("[data-vendor-icon]")).toBeNull();
    expect(screen.getByLabelText(/能力：/)).toBeTruthy();
    expect(screen.getByLabelText(/权限：/)).toBeTruthy();
    expect(screen.queryByLabelText("@ 引用")).toBeNull();
    expect(container.querySelector("[data-composer-name-row]")).toBeNull();
    const world = container.querySelector("[data-composer-world]");
    const card = container.querySelector('[data-composer-variant="card"]');
    const actions = container.querySelector("[data-composer-actions]");
    const sendCluster = container.querySelector("[data-composer-send-cluster]");
    const workspace = screen.getByLabelText("在哪工作");
    const model = screen.getByLabelText(/模型：/);
    const assembly = screen.getByLabelText(/能力：/);
    const permission = screen.getByLabelText(/权限：/);
    const send = screen.getByRole("button", { name: "发送" });
    const delegate = screen.getByRole("switch", { name: "统筹" });
    const attach = screen.getByRole("button", { name: "添加" });
    expect(world?.contains(workspace)).toBe(true);
    expect(world?.contains(assembly)).toBe(true);
    expect(world?.contains(delegate)).toBe(true);
    expect(world?.contains(permission)).toBe(true);
    expect(card?.contains(workspace)).toBe(false);
    expect(card?.contains(assembly)).toBe(false);
    expect(card?.contains(delegate)).toBe(false);
    expect(actions?.contains(attach)).toBe(true);
    expect(actions?.contains(send)).toBe(true);
    expect(sendCluster?.contains(model)).toBe(true);
    expect(sendCluster?.contains(delegate)).toBe(false);
    expect(sendCluster?.contains(send)).toBe(true);
    expect(sendCluster?.contains(workspace)).toBe(false);
    expect(sendCluster?.contains(assembly)).toBe(false);
    expect(delegate.getAttribute("aria-checked")).toBe("false");
    expect((delegate as HTMLButtonElement).disabled).toBe(false);
    expectWorldThenSend(container);
    const order = [...container.querySelectorAll("button, [aria-label]")];
    expect(order.indexOf(permission)).toBeGreaterThan(order.indexOf(workspace));
    expect(order.indexOf(assembly)).toBeGreaterThan(order.indexOf(permission));
    expect(order.indexOf(delegate)).toBeGreaterThan(order.indexOf(assembly));
    expect(order.indexOf(send)).toBeGreaterThan(order.indexOf(model));
  });

  it("＋ opens mention browse, does not insert @ into the body", () => {
    renderComposer();
    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    expect(openBrowseMock).toHaveBeenCalled();
    expect(mentionToggleMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("composer-body").textContent ?? "").not.toContain(
      "@",
    );
  });

  it("极简时统筹灰掉", () => {
    profileRows.data = [{ ...PROFILE_FULL }, { ...PROFILE_CHAT }];
    folderLists.conversations = [
      { ...listedConv("c-chat", null), assemblyId: "sys-chat" },
    ];
    seedOpenConversation("c-chat");
    renderComposer();
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    const btn = screen.getByRole("switch", { name: "统筹" });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    expect(btn.getAttribute("aria-checked")).toBe("false");
  });

  it("bar draft: world row stays visible; ＋ opens browse, not a settings dump", () => {
    renderComposer("bar");
    expect(
      document.querySelector('[data-composer-variant="bar"]'),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "更多" })).toBeNull();
    expect(screen.queryByTestId("composer-plus-menu")).toBeNull();
    expect(screen.getByRole("button", { name: "添加" })).toBeTruthy();
    expect(screen.getByLabelText("在哪工作")).toBeTruthy();
    expect(screen.getByLabelText(/模型：/)).toBeTruthy();
    expect(screen.getByLabelText(/能力：/)).toBeTruthy();
    expect(screen.getByLabelText(/权限：/)).toBeTruthy();
    expect(screen.queryByLabelText("@ 引用")).toBeNull();
    expect(screen.getByRole("switch", { name: "统筹" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "发送" })).toBeTruthy();
    const world = document.querySelector("[data-composer-world]");
    const card = document.querySelector('[data-composer-variant="bar"]');
    const actions = document.querySelector("[data-composer-actions]");
    expect(world?.contains(screen.getByLabelText("在哪工作"))).toBe(true);
    expect(card?.contains(screen.getByLabelText("在哪工作"))).toBe(false);
    expect(actions?.contains(screen.getByTestId("composer-body"))).toBe(true);
    expect(card?.contains(screen.getByTestId("composer-body"))).toBe(true);
    expect(card?.hasAttribute("data-composer-stacked")).toBe(false);
    expect(document.querySelector("[data-composer-name-row]")).toBeNull();
    expectWorldThenSend();
    expect(screen.queryByText("后台云端")).toBeNull();
    expect(screen.queryByRole("button", { name: /后台云端/ })).toBeNull();
  });

  it("有会话：世界行与＋收进更多，模型仍在底栏", () => {
    seedOpenConversation("c-open");
    renderComposer("bar");
    expect(document.querySelector("[data-composer-world]")).toBeNull();
    expect(screen.queryByRole("button", { name: "添加" })).toBeNull();
    expect(screen.queryByLabelText(/能力：/)).toBeNull();
    expect(screen.queryByRole("switch", { name: "统筹" })).toBeNull();
    const more = screen.getByRole("button", { name: "更多" });
    const actions = document.querySelector("[data-composer-actions]");
    expect(actions?.contains(more)).toBe(true);
    expect(actions?.contains(screen.getByLabelText(/模型：/))).toBe(true);

    fireEvent.click(more);
    const menu = screen.getByTestId("composer-plus-menu");
    const add = within(menu).getByTestId("composer-more-add");
    const permissionInMenu = within(menu).getByLabelText(/权限：/);
    const assemblyInMenu = within(menu).getByLabelText(/能力：/);
    const delegateInMenu = within(menu).getByRole("switch", { name: "统筹" });
    expect(add).toBeTruthy();
    const menuOrder = [...menu.querySelectorAll("button, [aria-label]")];
    expect(menuOrder.indexOf(permissionInMenu)).toBeGreaterThan(
      menuOrder.indexOf(add),
    );
    expect(menuOrder.indexOf(assemblyInMenu)).toBeGreaterThan(
      menuOrder.indexOf(permissionInMenu),
    );
    expect(menuOrder.indexOf(delegateInMenu)).toBeGreaterThan(
      menuOrder.indexOf(assemblyInMenu),
    );
    expect(menu.contains(screen.getByLabelText(/模型：/))).toBe(false);
  });

  it("有会话：更多第一行打开引用目录", () => {
    seedOpenConversation("c-open");
    renderComposer("bar");
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    fireEvent.click(screen.getByTestId("composer-more-add"));
    expect(openBrowseMock).toHaveBeenCalled();
    expect(mentionToggleMock).not.toHaveBeenCalled();
  });

  it("bar: 收到的上下文 sits outside the input card, not in send cluster or ＋ — even while streaming", () => {
    genMock.value = true;
    useConversationStore.setState({
      currentConversationId: OUTCOME_CID,
      byId: {
        [OUTCOME_CID]: {
          ...EMPTY_RUNTIME,
          isGenerating: true,
          messages: [
            {
              id: "a-live",
              role: "assistant",
              content: "",
              createdAt: new Date().toISOString(),
              executionId: null,
              isStreaming: true,
              captainContext: [
                {
                  channel: "request",
                  heading: "h",
                  body: "目标",
                  chars: 2,
                  truncated: false,
                  files: [],
                  source_role: "",
                  source_run_id: "",
                  fidelity: "",
                },
              ],
            },
          ],
        },
      },
    });
    renderComposer("bar");
    const card = document.querySelector('[data-composer-variant="bar"]');
    const endcap = document.querySelector('[data-composer-endcap="bar"]');
    const ctx = screen.getByTestId("composer-received-context");
    const stop = screen.getByRole("button", { name: "停止生成" });
    expect(card).toBeTruthy();
    expect(endcap).toBeTruthy();
    expect(card?.contains(ctx)).toBe(false);
    expect(card?.contains(stop)).toBe(true);
    expect(endcap?.contains(ctx)).toBe(true);
    expect(card?.parentElement?.nextElementSibling).toBe(endcap);
    expect(endcap?.classList.contains("absolute")).toBe(true);
    expect(endcap?.classList.contains("left-[calc(100%+0.25rem)]")).toBe(true);
    expect(endcap?.classList.contains("py-1")).toBe(true);
    expect(endcap?.classList.contains("items-end")).toBe(true);
    expect(ctx.classList.contains("size-8")).toBe(true);
    const shell = card?.parentElement?.parentElement;
    expect(shell?.getAttribute("data-composer-shell")).toBe("");
    expect(shell?.classList.contains("w-full")).toBe(true);
    expect(shell?.className).not.toContain("gap-1");
    const actions = card?.querySelector("[data-composer-actions]");
    expect(actions).toBeTruthy();
    expect(actions?.contains(ctx)).toBe(false);
    expect(screen.queryByTestId("composer-plus-menu")).toBeNull();
  });

  it("bar: 模型弹层直接从芯片打开", () => {
    renderComposer("bar");
    fireEvent.click(screen.getByLabelText(/模型：/));
    expect(screen.getByRole("listbox", { name: "模型" })).toBeTruthy();
    expect(
      screen
        .getByTestId("assembly-model-row")
        .getAttribute("data-shelf-layout"),
    ).toBe("columns");
    expect(
      screen
        .getByRole("listbox", { name: "模型" })
        .closest("[data-side]")
        ?.getAttribute("data-side"),
    ).toBe("top");
    expect(
      screen.getByTestId("composer-model-detail").getAttribute("data-side"),
    ).toBe("right");
    expect(screen.getByLabelText("在哪工作")).toBeTruthy();
  });

  it("narrow card: 模型弹层上下叠，不另开右侧卡", () => {
    const view = renderNarrowComposer("card");
    try {
      fireEvent.click(screen.getByLabelText(/模型：/));
      const list = screen.getByRole("listbox", { name: "模型" });
      const detail = screen.getByTestId("composer-model-detail");
      expect(
        screen
          .getByTestId("assembly-model-row")
          .getAttribute("data-shelf-layout"),
      ).toBe("stack");
      expect(detail.getAttribute("data-side")).toBeNull();
      expect(
        list.compareDocumentPosition(detail) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      expect(list.closest("[data-side]")?.className).toContain(
        "w-[min(20rem,calc(100vw-2rem))]",
      );
    } finally {
      view.restore();
    }
  });

  it("bar: 能力弹层直接从芯片打开", () => {
    renderComposer("bar");
    fireEvent.click(screen.getByLabelText(/能力：/));
    expect(screen.getByText("管理能力…")).toBeTruthy();
    expect(screen.getByLabelText("在哪工作")).toBeTruthy();
  });

  it("bar: 工作区弹层直接从芯片打开", () => {
    renderComposer("bar");
    fireEvent.click(screen.getByLabelText("在哪工作"));
    expect(screen.getByRole("button", { name: /云端对话/ })).toBeTruthy();
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
    expect(send.className).toContain("bg-muted");
    expect(send.className).toContain("size-8");
    expect(send.className).not.toContain("bg-primary");
    expect(send.className).not.toContain("bg-foreground");
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
    const stop = screen.getByRole("button", { name: "停止生成" });
    expect(stop.className).toContain("bg-foreground");
    expect(stop.className).toContain("size-8");
    expect(stop.className).not.toContain("bg-destructive");
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

  it("stopping after freeze (not generating): still 停止中…, no 发送", () => {
    genMock.value = false;
    useConversationStore.setState({
      byId: {
        [DRAFT_KEY]: { ...EMPTY_RUNTIME, turnPhase: "stopping" },
      },
    });
    renderComposer("bar");
    expect(screen.getByRole("button", { name: "停止中…" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "发送" })).toBeNull();
    expect(screen.queryByRole("button", { name: "停止生成" })).toBeNull();
  });

  it("stopping + draft: 发送 remains, no 排队/插队; stop button is 停止中…", async () => {
    genMock.value = true;
    useConversationStore.setState({
      byId: {
        [DRAFT_KEY]: { ...EMPTY_RUNTIME, turnPhase: "stopping" },
      },
    });
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "下一句");
    renderComposer("bar");
    const send = screen.getByRole("button", { name: "发送" });
    expect(send.getAttribute("title")).toBe("排队至本回合结束后发送");
    expect(screen.queryByRole("button", { name: "排队" })).toBeNull();
    expect(screen.queryByRole("button", { name: "排队发送" })).toBeNull();
    expect(screen.queryByRole("button", { name: "插队" })).toBeNull();
    expect(screen.getByRole("button", { name: "停止中…" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "停止生成" })).toBeNull();
  });

  it("streaming while generating: stop button is 停止生成 (not 停止中…)", () => {
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

  it("generating + draft: 发送 + 停止生成, no 排队/插队", async () => {
    genMock.value = true;
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "下一句");
    renderComposer("bar");
    const send = screen.getByRole("button", { name: "发送" });
    expect(send.className).toContain("bg-foreground");
    expect(send.className).not.toContain("bg-primary");
    expect(send.className).toContain("size-8");
    expect(send.getAttribute("title")).toBe("排队至本回合结束后发送");
    expect(screen.queryByRole("button", { name: "排队" })).toBeNull();
    expect(screen.queryByRole("button", { name: "排队发送" })).toBeNull();
    expect(screen.queryByRole("button", { name: "插队" })).toBeNull();
    expect(screen.getByRole("button", { name: "停止生成" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "插入" })).toBeNull();

    fireEvent.click(send);
    expect(handleSendMock).toHaveBeenCalledWith();
  });

  it("生成中：＋可点、粘贴与拖入照旧进附件收集", () => {
    genMock.value = true;
    const { container } = renderComposer();

    const attach = screen.getByRole("button", {
      name: "添加",
    }) as HTMLButtonElement;
    expect(attach.disabled).toBe(false);
    fireEvent.click(attach);
    expect(openBrowseMock).toHaveBeenCalled();

    const body = screen.getByTestId("composer-body");
    const root = container.querySelector("[data-composer-variant]");
    if (!root) throw new Error("composer root missing");

    fireEvent.paste(body);
    expect(dropMock.handlePaste).toHaveBeenCalled();
    fireEvent.drop(root);
    expect(dropMock.handleDrop).toHaveBeenCalled();
  });

  it("generating + draft: centered card also shows 发送 + 停止, no 排队", async () => {
    genMock.value = true;
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "下一句");
    renderComposer();
    expect(screen.getByRole("button", { name: "发送" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "排队" })).toBeNull();
    expect(screen.queryByRole("button", { name: "排队发送" })).toBeNull();
    expect(screen.getByRole("button", { name: "停止生成" })).toBeTruthy();
  });

  it("generating + draft: Ctrl/Cmd+Enter queues like Enter on classic prose", async () => {
    genMock.value = true;
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "插一句");
    renderComposer("bar");
    const body = screen.getByTestId("composer-body");
    fireEvent.keyDown(body, { key: "Enter", ctrlKey: true });
    expect(handleSendMock).toHaveBeenCalledWith({ delivery: "queue" });
  });

  it("经典工具步还在执行：Ctrl/Cmd+Enter 送进当前回合", async () => {
    genMock.value = true;
    useConversationStore.setState({
      currentConversationId: OUTCOME_CID,
      byId: {
        [OUTCOME_CID]: {
          ...EMPTY_RUNTIME,
          isGenerating: true,
          messages: [
            {
              id: "a-tool",
              role: "assistant",
              content: "",
              createdAt: new Date().toISOString(),
              executionId: null,
              isStreaming: true,
              process: [
                {
                  kind: "tool",
                  id: "t1",
                  tool_name: "read_file",
                  arguments: {},
                  result: null,
                  status: "running",
                },
              ],
            },
          ],
        },
      },
    });
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue(OUTCOME_CID, "改用另一份");
    renderComposer("bar");
    expect(
      screen.getByRole("button", { name: "发送" }).getAttribute("title"),
    ).toBe("排队至本回合结束后发送（Enter）；Ctrl/Cmd+Enter 送进当前回合");
    fireEvent.keyDown(screen.getByTestId("composer-body"), {
      key: "Enter",
      ctrlKey: true,
    });
    expect(handleSendMock).toHaveBeenCalledWith({ delivery: "steer" });
  });

  it("协调活跃 + 草稿：发送 + 停止，排队不另放字钮", async () => {
    genMock.value = true;
    seedCoordinationActive();
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue(OUTCOME_CID, "补一句");
    renderComposer("bar");
    expect(screen.getByRole("button", { name: "发送" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "排队" })).toBeNull();
    expect(screen.queryByRole("button", { name: "插队" })).toBeNull();
    expect(screen.getByRole("button", { name: "停止生成" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(handleSendMock).toHaveBeenCalledWith();
  });

  it("协调活跃 + 草稿：Enter 走默认，Ctrl/Cmd+Enter 送进当前回合", async () => {
    genMock.value = true;
    seedCoordinationActive();
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue(OUTCOME_CID, "补一句");
    renderComposer("bar");
    const body = screen.getByTestId("composer-body");
    fireEvent.keyDown(body, { key: "Enter" });
    expect(handleSendMock).toHaveBeenCalledWith();
    handleSendMock.mockClear();
    fireEvent.keyDown(body, { key: "Enter", ctrlKey: true });
    expect(handleSendMock).toHaveBeenCalledWith({ delivery: "steer" });
  });

  it("灯灭但队还在：发送 + 停止，无排队字钮", async () => {
    genMock.value = false;
    seedCoordinationLampOff();
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue(OUTCOME_CID, "补一句");
    renderComposer("bar");
    expect(screen.getByRole("button", { name: "发送" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "排队" })).toBeNull();
    expect(screen.queryByRole("button", { name: "插队" })).toBeNull();
    expect(screen.getByRole("button", { name: "停止生成" })).toBeTruthy();
  });

  it("idle: Ctrl/Cmd+Enter matches Enter (no fake queue)", async () => {
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "hello");
    renderComposer("bar");
    const body = screen.getByTestId("composer-body");
    fireEvent.keyDown(body, { key: "Enter", metaKey: true });
    expect(handleSendMock).toHaveBeenCalledWith();
  });

  it("Enter 换行：空闲 Enter 不发，Ctrl/Cmd+Enter 仍发", async () => {
    useUIStore.setState({ sendOnEnter: false });
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "hello");
    renderComposer("bar");
    const body = screen.getByTestId("composer-body");
    fireEvent.keyDown(body, { key: "Enter" });
    expect(handleSendMock).not.toHaveBeenCalled();
    fireEvent.keyDown(body, { key: "Enter", ctrlKey: true });
    expect(handleSendMock).toHaveBeenCalledWith();
  });

  it("Enter 换行：占用中 hint 不写 Enter 发送，裸 Enter 不排队", async () => {
    useUIStore.setState({ sendOnEnter: false });
    genMock.value = true;
    useConversationStore.setState({
      currentConversationId: OUTCOME_CID,
      byId: {
        [OUTCOME_CID]: {
          ...EMPTY_RUNTIME,
          isGenerating: true,
          messages: [
            {
              id: "a-tool",
              role: "assistant",
              content: "",
              createdAt: new Date().toISOString(),
              executionId: null,
              isStreaming: true,
              process: [
                {
                  kind: "tool",
                  id: "t1",
                  tool_name: "read_file",
                  arguments: {},
                  result: null,
                  status: "running",
                },
              ],
            },
          ],
        },
      },
    });
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue(OUTCOME_CID, "改用另一份");
    renderComposer("bar");
    expect(
      screen.getByRole("button", { name: "发送" }).getAttribute("title"),
    ).toBe("排队至本回合结束后发送；Ctrl/Cmd+Enter 送进当前回合");
    fireEvent.keyDown(screen.getByTestId("composer-body"), { key: "Enter" });
    expect(handleSendMock).not.toHaveBeenCalled();
  });

  it("live debate + generating: hides 排队/插队/出结论, send+stop", async () => {
    genMock.value = true;
    seedLiveDebate();
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue(OUTCOME_CID, "再问定价");
    renderComposer("bar");
    expect(screen.queryByRole("button", { name: "排队发送" })).toBeNull();
    expect(screen.queryByRole("button", { name: "插队" })).toBeNull();
    expect(screen.queryByRole("button", { name: "出结论" })).toBeNull();
    expect(screen.getByRole("button", { name: "发送" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "停止生成" })).toBeTruthy();
    expect(
      screen.getByRole("textbox", { name: COMPOSER_DEBATE_STEER_PLACEHOLDER }),
    ).toBeTruthy();

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

  it("live debate + empty draft: no 发送/出结论/排队/插队, stop only", () => {
    genMock.value = true;
    seedLiveDebate();
    renderComposer("bar");
    expect(screen.queryByRole("button", { name: "排队发送" })).toBeNull();
    expect(screen.queryByRole("button", { name: "插队" })).toBeNull();
    expect(screen.queryByRole("button", { name: "发送" })).toBeNull();
    expect(screen.queryByRole("button", { name: "出结论" })).toBeNull();
    expect(screen.getByRole("button", { name: "停止生成" })).toBeTruthy();
  });

  it("live debate + stopping after freeze: still 停止中…, no 发送", () => {
    genMock.value = false;
    seedLiveDebate();
    useConversationStore.setState({
      byId: {
        [OUTCOME_CID]: {
          ...useConversationStore.getState().byId[OUTCOME_CID],
          turnPhase: "stopping",
        },
      },
    });
    renderComposer("bar");
    expect(screen.getByRole("button", { name: "停止中…" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "发送" })).toBeNull();
  });

  it("live debate: no @ 入口 and no 添加; 更多仍在", () => {
    genMock.value = true;
    seedLiveDebate();
    const { unmount } = renderComposer();
    expect(screen.queryByLabelText("@ 引用")).toBeNull();
    expect(screen.queryByRole("button", { name: "添加" })).toBeNull();
    expect(screen.queryByTestId("composer-more-add")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    expect(screen.queryByTestId("composer-more-add")).toBeNull();
    expect(screen.getByLabelText(/能力：/)).toBeTruthy();
    unmount();

    renderComposer("bar");
    expect(screen.queryByLabelText("@ 引用")).toBeNull();
    expect(screen.queryByText("引用")).toBeNull();
    expect(screen.queryByRole("button", { name: "添加" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    expect(screen.queryByTestId("composer-more-add")).toBeNull();
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
    expect(screen.queryByRole("button", { name: "出结论" })).toBeNull();
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
    expect(send.className).toContain("bg-muted");
    expect(send.className).toContain("size-8");
    expect(send.className).not.toContain("bg-primary");
    expect(send.className).not.toContain("bg-foreground");
  });

  it("idle: draft fills 发送 with inverse ink", async () => {
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "试试效果");
    renderComposer("bar");
    const send = screen.getByRole("button", { name: "发送" });
    expect((send as HTMLButtonElement).disabled).toBe(false);
    expect(send.className).toContain("bg-foreground");
    expect(send.className).toContain("size-8");
    expect(send.className).not.toContain("bg-primary");
    expect(send.className).not.toContain("bg-muted");
  });

  it("does not show a live character count on card or bar", async () => {
    const { useComposerDraftStore } = await import("@/stores/composer");
    useComposerDraftStore.getState().setValue("__draft__", "试试效果");
    const { unmount } = renderComposer("card");
    expect(screen.queryByText(/^\d+字$/)).toBeNull();
    expect(screen.queryByText(/^\d+\/32,?000$/)).toBeNull();
    unmount();
    renderComposer("bar");
    expect(screen.queryByText(/^\d+字$/)).toBeNull();
    expect(screen.queryByText(/^\d+\/32,?000$/)).toBeNull();
  });

  it("has no composer textarea; body editor is the textbox", () => {
    const { container } = renderComposer();
    expect(container.querySelector("textarea")).toBeNull();
    expect(screen.getByTestId("composer-body")).toBeTruthy();
    expect(screen.getByRole("textbox")).toBeTruthy();
  });

  it("wide layout shows the default placeholder copy", () => {
    renderComposer();
    expect(screen.getByText(DEFAULT_PLACEHOLDER)).toBeTruthy();
  });

  it("narrow layout hides the default placeholder copy", () => {
    const view = renderNarrowComposer();
    try {
      expect(screen.queryByText(DEFAULT_PLACEHOLDER)).toBeNull();
      expect(
        screen.getByRole("textbox", { name: DEFAULT_PLACEHOLDER }),
      ).toBeTruthy();
    } finally {
      view.restore();
    }
  });

  it("narrow card: world row outside, actions keep ＋ and 发送", () => {
    const view = renderNarrowComposer("card");
    try {
      const world = document.querySelector("[data-composer-world]");
      const actions = document.querySelector("[data-composer-actions]");
      const workspace = screen.getByLabelText("在哪工作");
      const model = screen.getByLabelText(/模型：/);
      const assembly = screen.getByLabelText(/能力：/);
      expect(world).toBeTruthy();
      expect(world?.contains(workspace)).toBe(true);
      expect(world?.contains(assembly)).toBe(true);
      expect(
        world?.contains(screen.getByRole("switch", { name: "统筹" })),
      ).toBe(true);
      expect(world?.contains(screen.getByLabelText(/权限：/))).toBe(true);
      expect(world?.querySelector(".composer-fill-git")).toBeTruthy();
      expect(world?.contains(model)).toBe(false);
      expect(
        world?.contains(screen.getByRole("button", { name: "发送" })),
      ).toBe(false);
      expect(actions?.contains(model)).toBe(true);
      expect(
        actions?.contains(screen.getByRole("button", { name: "添加" })),
      ).toBe(true);
      expect(
        actions?.contains(screen.getByRole("button", { name: "发送" })),
      ).toBe(true);
      expect(
        actions?.contains(screen.getByRole("switch", { name: "统筹" })),
      ).toBe(false);
      expect(screen.queryByRole("button", { name: "更多" })).toBeNull();
      expect(screen.queryByLabelText("@ 引用")).toBeNull();
      expect(actions?.contains(screen.getByTestId("composer-body"))).toBe(
        false,
      );
    } finally {
      view.restore();
    }
  });

  it("narrow bar stacks the field above ＋ and 发送", () => {
    const view = renderNarrowComposer("bar");
    try {
      const card = document.querySelector('[data-composer-variant="bar"]');
      const actions = card?.querySelector("[data-composer-actions]");
      const body = screen.getByTestId("composer-body");
      const plus = screen.getByRole("button", { name: "添加" });
      const send = screen.getByRole("button", { name: "发送" });
      const model = screen.getByLabelText(/模型：/);
      expect(actions).toBeTruthy();
      expect(card?.hasAttribute("data-composer-stacked")).toBe(true);
      expect(card?.contains(body)).toBe(true);
      expect(actions?.contains(body)).toBe(false);
      expect(actions?.contains(plus)).toBe(true);
      expect(actions?.contains(model)).toBe(true);
      expect(actions?.contains(send)).toBe(true);
      expect(
        card?.querySelector("[data-composer-send-cluster]")?.contains(model),
      ).toBe(false);
      expect(
        body.compareDocumentPosition(actions as Node) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      expect(
        plus.compareDocumentPosition(send) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      expect(
        actions?.contains(screen.getByRole("switch", { name: "统筹" })),
      ).toBe(false);
      expect(
        document
          .querySelector("[data-composer-world]")
          ?.contains(screen.getByRole("switch", { name: "统筹" })),
      ).toBe(true);
      expect(document.querySelector("[data-composer-name-row]")).toBeNull();
      expect(screen.getByLabelText("在哪工作")).toBeTruthy();
      expect(
        document
          .querySelector("[data-composer-world]")
          ?.contains(screen.getByLabelText("在哪工作")),
      ).toBe(true);
    } finally {
      view.restore();
    }
  });

  it("narrow layout still shows the continue placeholder", () => {
    seedLastAssistant({ finishReason: "interrupted", content: "半成品" });
    const view = renderNarrowComposer();
    try {
      expect(screen.getByText(COMPOSER_CONTINUE_PLACEHOLDER)).toBeTruthy();
    } finally {
      view.restore();
    }
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
    expect(screen.getByTestId("composer-failure-banner").textContent).toContain(
      copy,
    );
    const body = screen.getByTestId("composer-body");
    expect(body.textContent ?? "").not.toContain(copy);
  });

  it("empty draft: falls back to session error when composer slot is empty", () => {
    const copy = "重新生成失败，请稍后重试";
    useConversationStore.getState().setError(copy, null);
    renderComposer();
    expect(screen.getByTestId("composer-failure-banner").textContent).toContain(
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
    expect(screen.queryByTestId("composer-failure-banner")).toBeNull();
    expect(
      useComposerSendErrorStore.getState().byKey.__draft__,
    ).toBeUndefined();
    expect(getActiveRuntime().error).toBeNull();
  });

  it("empty interrupt: composer hint is the sentence only", () => {
    seedLastAssistant({ finishReason: "interrupted", content: "" });
    renderComposer();
    const hint = screen.getByTestId("composer-failure-banner");
    expect(hint.textContent).toContain("发送下一条");
    expect(hint.textContent).toContain(COMPOSER_EMPTY_INTERRUPTED_HINT);
    expect(
      within(hint).queryByRole("button", { name: "复制排查包" }),
    ).toBeNull();
  });

  it("partial + rate-limit: composer banner hosts the why only", () => {
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
    const hint = screen.getByTestId("composer-failure-banner");
    expect(hint.textContent).toContain(LLM_RATE_LIMIT_WHY);
    expect(hint.textContent).not.toContain("发送下一条");
    expect(hint.textContent).not.toContain("未能交付");
    expect(
      within(hint).queryByRole("button", { name: "复制排查包" }),
    ).toBeNull();
  });

  it("empty interrupt: sessionError is suppressed (banner is the unique verdict)", () => {
    seedLastAssistant(
      { finishReason: "interrupted", content: "" },
      "网络中断，请重试。",
    );
    renderComposer();
    const banner = screen.getByTestId("composer-failure-banner");
    expect(banner.textContent).toContain("发送下一条");
    expect(banner.textContent).not.toContain("网络中断");
    expect(
      within(banner).queryByRole("button", { name: "复制排查包" }),
    ).toBeNull();
  });

  it("empty user-stop is not an error: no composer hint", () => {
    seedLastAssistant({ finishReason: "cancelled", content: "" });
    renderComposer();
    expect(screen.queryByTestId("composer-failure-banner")).toBeNull();
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
    expect(screen.queryByTestId("composer-failure-banner")).toBeNull();
  });

  it("session banner lights when the arbitrator has no other verdict", () => {
    seedLastAssistant(
      { finishReason: "end_turn", content: "已写出正文" },
      "网络中断，请重试。",
    );
    renderComposer();
    const banner = screen.getByTestId("composer-failure-banner");
    expect(banner.textContent).toContain("网络中断，请重试。");
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
  });

  it("turn failure owns the banner; session copy is not a second line", () => {
    seedLastAssistant(
      {
        finishReason: "error",
        content: "",
        error: { code: "LLM_ERROR", message: "模型调用失败，请重试。" },
      },
      "模型调用失败，请重试。",
    );
    renderComposer();
    const banners = screen.getAllByTestId("composer-failure-banner");
    expect(banners).toHaveLength(1);
    expect(banners[0]?.textContent).toContain("模型调用失败，请重试。");
  });

  it("closing a stall banner hides it and keeps the turn error", () => {
    seedLastAssistant({
      finishReason: "error",
      content: "",
      error: {
        code: "LLM_TIMEOUT",
        message: "模型流式响应停滞（长时间无输出），请稍后重试",
      },
    });
    renderComposer();
    expect(screen.getByTestId("composer-failure-banner").textContent).toContain(
      "模型流式响应停滞",
    );
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByTestId("composer-failure-banner")).toBeNull();
    const last = getActiveRuntime().messages.at(-1);
    expect(last?.role).toBe("assistant");
    if (last?.role === "assistant") {
      expect(last.error?.message).toContain("模型流式响应停滞");
    }
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
    const banner = screen.getByTestId("composer-failure-banner");
    expect(banner.textContent).toContain("发送失败：没有可用的模型密钥");
    expect(banner.textContent).not.toContain("模型调用失败");
  });

  it("composerError after bubbles are gone is the sentence only", () => {
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
    expect(screen.getByTestId("composer-failure-banner").textContent).toContain(
      "上游限流",
    );
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
  });

  it("interrupted-with-body: continue placeholder, no empty-interrupt hint", () => {
    seedLastAssistant({ finishReason: "interrupted", content: "半成品" });
    renderComposer();
    expect(
      screen.getByRole("textbox", { name: COMPOSER_CONTINUE_PLACEHOLDER }),
    ).toBeTruthy();
    expect(screen.queryByTestId("composer-failure-banner")).toBeNull();
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
    expect(send.className).toContain("bg-foreground");
    expect(send.className).not.toContain("bg-primary");
    fireEvent.click(send);
    expect(handleSendMock).not.toHaveBeenCalled();
  });

  it("viewer on 与我共享 desk: 发送 disabled + title；Enter 不发", async () => {
    const { useComposerDraftStore } = await import("@/stores/composer");
    seedOpenConversation("c-view");
    folderLists.conversations = [listedConv("c-view", "f-shared")];
    folderLists.accessible = [cloudDesk("f-shared", "队友桌", "viewer")];
    folderLists.grouped = [];
    useComposerDraftStore.getState().setValue("c-view", "想发一条");
    renderComposer("bar");
    expect(screen.getByTestId("composer-body")).toBeTruthy();
    const send = screen.getByRole("button", { name: "发送" });
    expect((send as HTMLButtonElement).disabled).toBe(true);
    expect(send.className).toContain("bg-muted");
    expect(send.getAttribute("title")).toBe(COMPOSER_FOLDER_READ_ONLY_HINT);
    fireEvent.keyDown(screen.getByTestId("composer-body"), { key: "Enter" });
    expect(handleSendMock).not.toHaveBeenCalled();
  });

  it("viewer draft folder intent: 发送 disabled + title", async () => {
    const { useComposerDraftStore } = await import("@/stores/composer");
    folderLists.accessible = [cloudDesk("f-view", "只读桌", "viewer")];
    useFoldersStore.setState({
      draftWorkspaceIntent: { kind: "folder", folderId: "f-view" },
    });
    useComposerDraftStore.getState().setValue("__draft__", "草稿");
    renderComposer("bar");
    const send = screen.getByRole("button", { name: "发送" });
    expect((send as HTMLButtonElement).disabled).toBe(true);
    expect(send.getAttribute("title")).toBe(COMPOSER_FOLDER_READ_ONLY_HINT);
  });

  it("viewer generating: 发送 disabled, no 排队；Enter 不发", async () => {
    genMock.value = true;
    const { useComposerDraftStore } = await import("@/stores/composer");
    seedOpenConversation("c-view");
    folderLists.conversations = [listedConv("c-view", "f-shared")];
    folderLists.accessible = [cloudDesk("f-shared", "队友桌", "viewer")];
    useComposerDraftStore.getState().setValue("c-view", "下一句");
    renderComposer("bar");
    expect(screen.queryByRole("button", { name: "排队" })).toBeNull();
    expect(screen.queryByRole("button", { name: "插队" })).toBeNull();
    const send = screen.getByRole("button", { name: "发送" });
    expect((send as HTMLButtonElement).disabled).toBe(true);
    expect(send.getAttribute("title")).toBe(COMPOSER_FOLDER_READ_ONLY_HINT);
    fireEvent.keyDown(screen.getByTestId("composer-body"), { key: "Enter" });
    fireEvent.keyDown(screen.getByTestId("composer-body"), {
      key: "Enter",
      ctrlKey: true,
    });
    expect(handleSendMock).not.toHaveBeenCalled();
  });

  it("owner / editor / 裸聊 / 找不到 folder: 发送仍可用", async () => {
    const { useComposerDraftStore } = await import("@/stores/composer");

    seedOpenConversation("c-own");
    folderLists.conversations = [listedConv("c-own", "f-own")];
    folderLists.accessible = [cloudDesk("f-own", "我的桌", "owner")];
    useComposerDraftStore.getState().setValue("c-own", "可发");
    const own = renderComposer("bar");
    expect(
      (screen.getByRole("button", { name: "发送" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    own.unmount();

    seedOpenConversation("c-edit");
    folderLists.conversations = [listedConv("c-edit", "f-edit")];
    folderLists.accessible = [cloudDesk("f-edit", "可编桌", "editor")];
    useComposerDraftStore.getState().setValue("c-edit", "可发");
    const edit = renderComposer("bar");
    expect(
      (screen.getByRole("button", { name: "发送" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    edit.unmount();

    useConversationStore.setState({
      currentConversationId: null,
      byId: {},
    } as never);
    folderLists.conversations = [];
    folderLists.accessible = [];
    useFoldersStore.setState({
      draftWorkspaceIntent: { kind: "quick_cloud" },
    });
    useComposerDraftStore.getState().setValue("__draft__", "裸聊");
    const bare = renderComposer("bar");
    expect(
      (screen.getByRole("button", { name: "发送" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    bare.unmount();

    seedOpenConversation("c-gone");
    folderLists.conversations = [listedConv("c-gone", "f-missing")];
    folderLists.accessible = [];
    useComposerDraftStore.getState().setValue("c-gone", "仍可发");
    renderComposer("bar");
    expect(
      (screen.getByRole("button", { name: "发送" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("原生壳空草稿＋先选来源，引用才打开目录", () => {
    capState.native = true;
    renderComposer();
    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    expect(openBrowseMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("attach-source-menu")).toBeTruthy();
    expect(
      screen.getByTestId("attach-source-input-camera").getAttribute("capture"),
    ).toBe("environment");
    fireEvent.click(screen.getByTestId("attach-source-cite"));
    expect(openBrowseMock).toHaveBeenCalled();
  });

  it("原生壳有会话：更多第一行是拍照/相册/文件/引用", () => {
    capState.native = true;
    seedOpenConversation("c-open");
    renderComposer("bar");
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    const menu = screen.getByTestId("composer-plus-menu");
    expect(within(menu).getByTestId("attach-source-camera")).toBeTruthy();
    expect(within(menu).getByTestId("attach-source-gallery")).toBeTruthy();
    expect(within(menu).getByTestId("attach-source-files")).toBeTruthy();
    expect(within(menu).queryByTestId("composer-more-add")).toBeNull();
    fireEvent.click(within(menu).getByTestId("attach-source-cite"));
    expect(openBrowseMock).toHaveBeenCalled();
  });
});
