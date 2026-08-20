/**
 * Unified conversation side panel types & constants (前端UX设计.md §十).
 * ONE flat tab strip (方案 B · 图1式):
 *
 *  - 「工作区」(first)：不可销毁、可 detach 为应用内浮窗；
 *  - 「改动」(second)：§十 P0c 条件固定（有货才审），出现后不可关、可 detach；
 *  - closable content tabs (≤12, 固定不计): File 多实例、Terminal / Browser 各一壳
 *   （壳内各自管会话/页签）、run / endpoint / simple-turn 详情。
 *
 * 应用内浮窗（§十 · 方案 B）：Move 不 Copy；可 float = run / workspace / file / changes；
 * 不可 = terminal / browser；content / simple-turn 永不 float。统一上限 8。
 *
 * Content tabs store references only; bodies keep-alive while the tab exists.
 * `open` / `width` are persisted; content tabs + floats are session-level.
 */

/** Resize bounds for the panel. */
export const MIN_WIDTH = 280;
/**
 * 面板宽度上限改为「相对窗口」的动态值（行业主流：VS Code 靠主区最小宽反向约束、
 * Claude Artifacts / CSS clamp 用窗口百分比）——固定像素上限在大屏太窄、在小屏又会
 * 挤压中间对话区。上限 = min(硬上限, 窗口宽 × 比例)，随窗口自适应。
 */
export const MAX_WIDTH_RATIO = 0.6;
/** 超宽屏兜底：再宽的显示器也不让单个面板超过此像素，避免主区被过度挤压。 */
export const MAX_WIDTH_CAP = 960;
/** window 不可用时（无布局环境 / 早期单测）估算视口用的兜底宽度。 */
export const FALLBACK_VIEWPORT = 1280;
export const DEFAULT_WIDTH = 400;

/** Cap on closable content tabs: opening beyond the limit drops the oldest (fixed tabs exempt). */
export const MAX_TABS = 12;
/** Cap on simultaneous in-app floats (前端UX设计.md §十); reject further float until dock/close. */
export const MAX_FLOATS = 8;

export const OPEN_KEY = "side-panel-open";
export const WIDTH_KEY = "side-panel-width";

/** Default float chrome size (session-level; persist across restart is 二期). */
export const DEFAULT_FLOAT_WIDTH = 480;
export const DEFAULT_FLOAT_HEIGHT = 560;
export const FLOAT_CASCADE_OFFSET = 28;

/**
 * 当前视口下的面板宽度上限：min(硬上限, 窗口宽 × 比例)，且不低于 MIN_WIDTH（极窄窗口）。
 * 动态值，故导出为函数而非常量——拖拽 clamp、窗口 resize 收敛、双击复位都以它为准。
 */
export function sidePanelMaxWidth(): number {
  const viewport =
    typeof window !== "undefined" && window.innerWidth
      ? window.innerWidth
      : FALLBACK_VIEWPORT;
  return Math.max(
    MIN_WIDTH,
    Math.min(MAX_WIDTH_CAP, Math.round(viewport * MAX_WIDTH_RATIO)),
  );
}

export const SIDE_PANEL_MIN_WIDTH = MIN_WIDTH;
export const SIDE_PANEL_DEFAULT_WIDTH = DEFAULT_WIDTH;
export const SIDE_PANEL_MAX_TABS = MAX_TABS;
export const SIDE_PANEL_MAX_FLOATS = MAX_FLOATS;

/** Reserved id of the fixed 「工作区」 home tab (always first; 不可销毁、可 detach). */
export const WORKSPACE_TAB_ID = "workspace";

/**
 * Reserved id of the 「改动」 tab（§十 · P0c 条件固定：本对话有可恢复入口 /
 * 深链 / 已 float / 当前正在看时挂上；出现后不可关、可 detach）。
 * 有货才审，空态不常驻。
 */
export const CHANGES_TAB_ID = "changes";

/**
 * Stable content-tab id for the 右坞浏览器壳（顶栏可关内容 tab；`+` / 活动卡共用）。
 * 产物 HTML 完整预览亦走本 tab（`openWorkspaceHtmlInBrowser`）；旧平行「预览」tab 已拆除（M3b）。
 */
export const TEAM_BROWSER_TAB_ID = "browser:team";

/**
 * Stable content-tab id for the 右坞终端壳（顶栏可关；`+` / 后台进程活动共用）。
 * 多 pty / 后台进程 / 执行记录在壳内列表管理，不另开顶栏 tab。
 */
export const TEAM_TERMINAL_TAB_ID = "terminal:hub";

/** Auto-surface dismiss key for the terminal hub (scoped per conversation). */
export function terminalDismissKey(conversationId: string | null): string {
  return conversationId ? `terminal:${conversationId}` : "terminal";
}

/** Auto-surface dismiss key for the browser hub (scoped per conversation). */
export function browserDismissKey(conversationId: string | null): string {
  return conversationId ? `browser:${conversationId}` : "browser";
}

/**
 * A run-detail tab — one per revision chain (tab id = chain root) or standalone
 * run. Clicking an inline graph node pins that run here (前端UX设计.md §十);
 * switching rounds/chips updates `runId` in place without a new tab. Scoped by
 * message so two turns that each pin a run never collide in the strip (§9.3).
 */
export interface RunDetailTab {
  /** Discriminator: a worker run's structured detail (RunDetailBody). */
  kind: "run";
  /** Dedup identity: `run-detail:<messageId>:<chainRootOrRunId>`. */
  id: string;
  /** Label shown in the tab strip (the agent's role). */
  title: string;
  /** The assistant message whose execution slot holds this run. */
  messageId: string;
  /** The run currently shown in this tab (may be a revision of the chain root). */
  runId: string;
}

/** Which endpoint a content tab stands for — drives its tab-strip icon (提问 vs
 * 最终回答), mirroring the graph endpoint nodes (用户输入 / CEO 汇聚点). */
export type EndpointKind = "prompt" | "answer";

/**
 * A content tab — the turn's endpoint chat bubble (the user's prompt or the CEO's
 * final answer) surfaced in the docked panel. The 全屏放大态 has no
 * chat column alongside, so an endpoint reads here — like a worker drill — instead
 * of a foot drawer (协作图与双视图UX.md §六 两个入口：聊天内嵌 ⇄ 全屏放大). Endpoints are bubbles, not runs, so they
 * ride this kind rather than RunDetailBody. Scoped by the turn (`messageId`) so it
 * lights that graph's endpoint node; `contentMessageId` is the bubble rendered.
 */
export interface ContentDetailTab {
  /** Discriminator: a chat bubble rendered as Markdown (no run). */
  kind: "content";
  /** Dedup identity: `content-detail:<messageId>:<contentMessageId>`. */
  id: string;
  /** Label shown in the tab strip (提问 / 最终回答). */
  title: string;
  /** The turn (assistant message owning the execution) this endpoint belongs to. */
  messageId: string;
  /** The chat message whose content is rendered (the prompt / the final answer). */
  contentMessageId: string;
  /** The endpoint this bubble stands for — the user's prompt / the CEO's answer. */
  endpoint: EndpointKind;
}

/**
 * A simple-turn Q&A tab — the whole CEO-only exchange (user prompt + assistant
 * answer) for a no-execution turn. Pure dialogue has no execution
 * plan, so it must not ride `content` (whose live check requires a plan) or
 * `run` (前端UX设计.md §十 详情面板（右坞）).
 */
export interface SimpleTurnDetailTab {
  /** Discriminator: full Q&A for a no-execution turn. */
  kind: "simple-turn";
  /** Dedup identity: `simple-turn:<messageId>`. */
  id: string;
  /** Label shown in the tab strip (对话). */
  title: string;
  /** The turn key (assistant projection id) this Q&A belongs to. */
  messageId: string;
  /** The user message bubble rendered under 「提问」. */
  promptMessageId: string;
  /** The assistant message bubble rendered under 「回答」. */
  answerMessageId: string;
}

/** Top-bar Terminal content tab — singleton hub; sessionId focuses a pty inside. */
export interface TerminalDetailTab {
  kind: "terminal";
  /** Dedup identity: always {@link TEAM_TERMINAL_TAB_ID}. */
  id: string;
  title: string;
  /** Preferred pty session to select inside the hub; null = panel default selection. */
  sessionId: string | null;
}

/** Top-bar File content tab — path reference only; body keep-alives FileDetail. */
export interface FileDetailTab {
  kind: "file";
  /** Dedup identity: `file:<path>` or `file:<workspaceId>:<path>` when desk-scoped. */
  id: string;
  title: string;
  path: string;
  name: string;
  /**
   * 落地 desk（`folder:…` / `conv:…`）。产物预览跟落地桌；缺省由渲染层回退会话出生桌。
   * 不同桌同路径必须是两个 tab（见 {@link fileTabId}）。
   */
  workspaceId?: string;
}

/**
 * Top-bar Browser content tab — 右坞 BrowserPanel 壳。
 * 能力上通常一会话一实例；壳内多页签由 browserSessions store 管理。
 */
export interface BrowserDetailTab {
  kind: "browser";
  id: string;
  title: string;
}

/** A side-panel content tab (详情 / 终端 / 文件 / 浏览器). */
export type DetailTab =
  | RunDetailTab
  | ContentDetailTab
  | SimpleTurnDetailTab
  | TerminalDetailTab
  | FileDetailTab
  | BrowserDetailTab;

/** Kinds that may leave the dock as an in-app float (Move). */
export type FloatableTabKind = "run" | "file" | "workspace" | "changes";

/** Session-level geometry for one float chrome (落盘二期). */
export interface FloatLayout {
  x: number;
  y: number;
  width: number;
  height: number;
  /** Stacking order; focus bumps to max+1. */
  zIndex: number;
}

/** One Move'd panel currently shown as an in-app float. */
export interface SidePanelFloat {
  tabId: string;
  layout: FloatLayout;
}

/**
 * Focus surface for graph / run highlight (前端UX设计.md §十):
 * dock active tab, or a specific float — must not require `open === true`.
 */
export type SidePanelFocusSurface =
  | { type: "dock" }
  | { type: "float"; tabId: string };

/** Tab-strip id for a run detail. Prefer the continuation-chain root so all beats
 * of the same speaker share one tab; pass the root (or the run itself when it
 * has no `continuesRunId`). */
export const runDetailTabId = (messageId: string, runId: string): string =>
  `run-detail:${messageId}:${runId}`;

export const contentDetailTabId = (
  messageId: string,
  contentMessageId: string,
): string => `content-detail:${messageId}:${contentMessageId}`;

export const simpleTurnDetailTabId = (messageId: string): string =>
  `simple-turn:${messageId}`;

/** File tab identity — path alone for session-desk opens; include desk when scoped. */
export const fileTabId = (path: string, workspaceId?: string | null): string =>
  workspaceId ? `file:${workspaceId}:${path}` : `file:${path}`;

/** Tab id that currently owns focus for highlighting. */
export function sidePanelFocusTabId(state: {
  focusSurface: SidePanelFocusSurface;
  activeTabId: string;
}): string {
  return state.focusSurface.type === "float"
    ? state.focusSurface.tabId
    : state.activeTabId;
}

export interface SidePanelState {
  /** Panel visibility (persisted). */
  open: boolean;
  /** Docked width in px, clamped to [280, 动态上限] (persisted)；上限见 sidePanelMaxWidth()。 */
  width: number;
  /** Open content tabs (session-level; 固定 工作区 / 改动 不在此数组). */
  tabs: DetailTab[];
  /**
   * Active dock tab: `WORKSPACE_TAB_ID` / `CHANGES_TAB_ID`
   * or a content tab id. Defaults to the workspace home. Floating tabs are not
   * the dock active surface (see {@link focusSurface}).
   */
  activeTabId: string;
  /**
   * In-app floats currently Move'd out of the dock (session-level; ≤ {@link SIDE_PANEL_MAX_FLOATS}).
   * Same tab id appears in at most one place (docked XOR floating).
   */
  floats: SidePanelFloat[];
  /**
   * Which surface owns interaction focus for highlight (dock active vs a float).
   * Independent of `open` — closing the dock must not clear a float focus.
   */
  focusSurface: SidePanelFocusSurface;
  /**
   * 「改动」tab 聚焦的回合（产物卡「查看改动」写入）。
   * 切对话时应清掉（避免旧 messageId 在新对话上错误聚焦）。
   */
  changesFocusMessageId: string | null;
  /**
   * Session-level memory of contexts where the user explicitly closed the panel,
   * blocking auto-surface until the panel is opened again or the context clears.
   */
  dismissedContexts: Set<string>;
  /**
   * Count of auto-surface events suppressed while the panel was dismissed — shown
   * as a badge on the panel toggle when the dock is closed.
   */
  pendingBadge: number;

  /** Record that auto-surface should not reopen the panel for this context. */
  dismissAutoSurface: (contextId: string) => void;
  isAutoSurfaceDismissed: (contextId: string) => boolean;
  clearAutoSurfaceDismiss: (contextId: string) => void;
  /** Bump the toggle badge when auto-surface is blocked by a dismiss. */
  incrementPendingBadge: () => void;

  /** Open (or re-focus) a content tab, deduped by id; reveals + activates it. */
  openTab: (
    tab: DetailTab,
    opts?: { activate?: boolean; reveal?: boolean },
  ) => void;
  /** Close a content tab; falls back to a neighbour tab, else the 工作区 home.
   * Never closes the panel (fixed tabs are always there). */
  closeTab: (id: string) => void;
  /**
   * 重排 `tabs` 中出现在 `orderedIds` 里的项：抽出后按 orderedIds 排序，再写回原索引位。
   * 不在 orderedIds 的 tab（含已 float）相对位置不变。校验失败则 no-op。
   */
  reorderContentTabs: (orderedIds: string[]) => void;
  /** Activate a dock tab, or focus the float if that id is floating (Move). */
  setActiveTab: (id: string) => void;
  /**
   * Pin a run (of a specific message's turn) and reveal it. The inline graph
   * highlights whatever run tab is active for that turn, so opening / switching
   * / closing tabs keeps the graph in sync (§9.3).
   */
  showRunDetail: (messageId: string, runId: string, title?: string) => void;
  /**
   * Pin an endpoint chat bubble (the turn's prompt / final answer) and reveal it.
   * The 全屏放大态 surfaces an endpoint here (no chat column alongside); the inline
   * graph lights the matching endpoint node while its content tab is active.
   */
  showContentDetail: (
    messageId: string,
    contentMessageId: string,
    title: string,
    endpoint: EndpointKind,
  ) => void;
  /**
   * Pin a simple-turn Q&A (user prompt + assistant answer) and reveal it. Used for
   * no-execution turns — not a run/content tab.
   */
  showSimpleTurnDetail: (
    messageId: string,
    promptMessageId: string,
    answerMessageId: string,
    title?: string,
  ) => void;
  /**
   * Drop every reading-context tab (endpoint content + simple-turn Q&A), keeping
   * run / terminal / file / browser tabs. TurnDetailPage calls this on unmount
   * (放大态 exit).
   */
  closeContentTabs: () => void;
  /**
   * 切对话：卸对话作用域内容 tab（run / endpoint content / simple-turn / file），
   * 保留 terminal / browser 壳；固定 工作区 / 改动 不在 `tabs` 内故不受影响。
   * 不改 `open` / `width`；浮窗壳由 {@link clearFloats} 负责，本 API 仅顺带摘掉
   * 已卸 tab 对应的 float 条目（可与 clearFloats 同 effect 先后调用）。
   */
  closeConversationScopedTabs: () => void;
  /**
   * Reveal the panel WITHOUT touching the active tab — so a newly-arrived
   * decision can open the dock while a run/workspace tab the user is reading
   * stays put.
   */
  openPanel: () => void;
  /** Reveal the panel on the 工作区 home tab (the chat toggle / Ctrl+J). */
  showWorkspace: () => void;
  /**
   * 揭示面板并激活「改动」tab（条件固定；无货时仍可先挂再看）；可选聚焦某回合。
   */
  showChanges: (messageId?: string | null) => void;
  /** 清除改动深链聚焦（切对话时调用）。 */
  clearChangesFocus: () => void;
  /**
   * Open / focus a File content tab (path reference); reveals the panel.
   * Optional `workspaceId` scopes the tab to that landing desk (产物预览跟落地桌).
   */
  showFile: (path: string, name: string, workspaceId?: string | null) => void;
  /**
   * `+` → 文件：无路径时合理空态（打开一个占位文件 tab，提示从工作区点选）。
   * 有路径时等同 {@link showFile}。
   */
  openFileTab: (
    path?: string,
    name?: string,
    workspaceId?: string | null,
  ) => void;
  /** `+` → 终端：开/聚焦唯一 Terminal 壳；可选绑定 preferred session。 */
  openTerminalTab: (opts?: {
    sessionId?: string | null;
    title?: string;
    activate?: boolean;
    reveal?: boolean;
  }) => string;
  /** Update the hub tab's preferred session (after async spawn). */
  bindTerminalSession: (
    tabId: string,
    sessionId: string,
    title?: string,
  ) => void;
  /** Clear hub preferredSessionId when that pty was closed. */
  clearTerminalPreferredSession: (sessionId: string) => void;
  /**
   * 揭示「浏览器」内容 tab（活动卡 / 登录升级卡 / `+` / 产物完整预览）：开面板 + 开/聚焦浏览器壳；
   * 无本地页签时建空白页。
   */
  showBrowser: () => void;
  closePanel: () => void;
  togglePanel: () => void;
  setWidth: (width: number) => void;
  /** 窗口尺寸变化后把当前宽度收敛到新的动态上限（仅在越界时写入）。 */
  reclampWidth: () => void;
  /** 双击 resize 手柄：在 最小 / 默认 / 最大 三档间循环（窄屏 default==max 时自动去重）。 */
  cycleWidth: () => void;

  /** True when `tabId` is currently Move'd to an in-app float. */
  isFloating: (tabId: string) => boolean;
  /**
   * Move a floatable tab out of the dock. Returns false when kind is not floatable
   * or the unified float cap (8) is full (must dock/close first). Re-float of an
   * already-floating tab focuses it and returns true.
   */
  floatTab: (
    tabId: string,
    layout?: Partial<Omit<FloatLayout, "zIndex">>,
  ) => boolean;
  /**
   * Pin a float back to the dock (VS Code default on closing a float). Opens the
   * dock and activates the tab. No-op when not floating.
   */
  dockTab: (tabId: string) => void;
  /**
   * Explicitly destroy a closable float (run / file). Workspace / changes cannot
   * be destroyed — use {@link dockTab}. Returns false when rejected.
   */
  destroyFloat: (tabId: string) => boolean;
  /**
   * Clear all floats (切/删对话). Destroys floating content tabs; fixed tabs only
   * lose float placement. Page layer calls this — ConversationPage not wired here.
   */
  clearFloats: () => void;
  /** Update session-level float geometry. */
  setFloatLayout: (
    tabId: string,
    layout: Partial<Omit<FloatLayout, "zIndex">>,
  ) => void;
  /** Mark a float as the focus surface (bring-to-front + highlight). */
  focusFloat: (tabId: string) => void;
  /** Mark the dock as the focus surface (uses {@link activeTabId}). */
  focusDock: () => void;
}

export type SidePanelSet = (
  partial:
    | Partial<SidePanelState>
    | ((state: SidePanelState) => Partial<SidePanelState> | SidePanelState),
  replace?: false,
) => void;

export type SidePanelGet = () => SidePanelState;
