import { useBrowserSessionsStore } from "../browserSessions";
import { useConversationStore } from "../conversation";
import {
  projectRuntime,
  revisionRootId,
  useExecutionStore,
} from "../execution";
import { canRevealSidePanel, persistOpen } from "./chrome";
import { floatingIdSet } from "./helpers";
import {
  CHANGES_TAB_ID,
  type SidePanelGet,
  type SidePanelSet,
  type SidePanelState,
  TEAM_BROWSER_TAB_ID,
  TEAM_TERMINAL_TAB_ID,
  type TerminalDetailTab,
  WORKSPACE_TAB_ID,
  browserDismissKey,
  contentDetailTabId,
  fileTabId,
  runDetailTabId,
  simpleTurnDetailTabId,
  terminalDismissKey,
} from "./types";

let untitledFileSeq = 0;
function nextUntitledFileId(): string {
  untitledFileSeq += 1;
  return `u${untitledFileSeq}`;
}

type FacadeActions = Pick<
  SidePanelState,
  | "showRunDetail"
  | "showContentDetail"
  | "showSimpleTurnDetail"
  | "showWorkspace"
  | "showChanges"
  | "closeChanges"
  | "clearChangesFocus"
  | "showFile"
  | "openFileTab"
  | "openTerminalTab"
  | "bindTerminalSession"
  | "clearTerminalPreferredSession"
  | "showBrowser"
>;

/** Entry-point facades: show* / open* for each surface kind. */
export function createFacadeActions(
  set: SidePanelSet,
  get: SidePanelGet,
): FacadeActions {
  return {
    showRunDetail: (messageId, runId, title) => {
      // Same revision chain → one tab keyed by the chain root; `runId` tracks the
      // beat currently shown (graph node / 轮次 chip). Non-revision runs keep a
      // 1:1 tab. If the turn isn't projected yet, fall back to the clicked id.
      const rt = useExecutionStore.getState().byId[messageId];
      const projected = rt ? projectRuntime(rt) : null;
      const tabKeyRunId = projected
        ? revisionRootId(runId, projected.runs)
        : runId;
      get().openTab({
        kind: "run",
        id: runDetailTabId(messageId, tabKeyRunId),
        title: title ?? "详情",
        messageId,
        runId,
      });
    },

    showContentDetail: (messageId, contentMessageId, title, endpoint) => {
      get().openTab({
        kind: "content",
        id: contentDetailTabId(messageId, contentMessageId),
        title,
        messageId,
        contentMessageId,
        endpoint,
      });
    },

    showSimpleTurnDetail: (
      messageId,
      promptMessageId,
      answerMessageId,
      title,
    ) => {
      get().openTab({
        kind: "simple-turn",
        id: simpleTurnDetailTabId(messageId),
        title: title ?? "对话",
        messageId,
        promptMessageId,
        answerMessageId,
      });
    },

    showWorkspace: () => {
      if (get().isFloating(WORKSPACE_TAB_ID)) {
        get().focusFloat(WORKSPACE_TAB_ID);
        return;
      }
      if (!canRevealSidePanel()) return;
      persistOpen(true);
      set({
        open: true,
        activeTabId: WORKSPACE_TAB_ID,
        pendingBadge: 0,
        focusSurface: { type: "dock" },
      });
    },

    showChanges: (messageId) => {
      if (get().isFloating(CHANGES_TAB_ID)) {
        set({
          changesOpen: true,
          changesFocusMessageId: messageId ?? null,
        });
        get().focusFloat(CHANGES_TAB_ID);
        return;
      }
      if (!canRevealSidePanel()) return;
      persistOpen(true);
      set({
        open: true,
        activeTabId: CHANGES_TAB_ID,
        changesOpen: true,
        changesFocusMessageId: messageId ?? null,
        pendingBadge: 0,
        focusSurface: { type: "dock" },
      });
    },

    closeChanges: () => {
      set((s) => {
        const floats = s.floats.filter((f) => f.tabId !== CHANGES_TAB_ID);
        let activeTabId = s.activeTabId;
        let focusSurface = s.focusSurface;
        if (s.activeTabId === CHANGES_TAB_ID) {
          const floatingIds = floatingIdSet(floats);
          const dockedContent = s.tabs.filter((t) => !floatingIds.has(t.id));
          activeTabId =
            dockedContent[dockedContent.length - 1]?.id ?? WORKSPACE_TAB_ID;
        }
        if (
          focusSurface.type === "float" &&
          focusSurface.tabId === CHANGES_TAB_ID
        ) {
          focusSurface = { type: "dock" };
        }
        if (
          !s.changesOpen &&
          s.changesFocusMessageId == null &&
          floats.length === s.floats.length &&
          activeTabId === s.activeTabId &&
          focusSurface === s.focusSurface
        ) {
          return s;
        }
        return {
          changesOpen: false,
          changesFocusMessageId: null,
          floats,
          activeTabId,
          focusSurface,
        };
      });
    },

    clearChangesFocus: () => {
      set((s) =>
        s.changesFocusMessageId == null ? s : { changesFocusMessageId: null },
      );
    },

    showFile: (path, name, workspaceId) => {
      get().openFileTab(path, name, workspaceId);
    },

    openFileTab: (path, name, workspaceId) => {
      if (path && name) {
        const desk = workspaceId?.trim() || undefined;
        get().openTab({
          kind: "file",
          id: fileTabId(path, desk),
          title: name,
          path,
          name,
          ...(desk ? { workspaceId: desk } : {}),
        });
        return;
      }
      // `+` → 文件无路径：占位空态 tab（可多开；不与真实路径 file: 冲突）。
      const instanceId = nextUntitledFileId();
      get().openTab({
        kind: "file",
        id: `file:untitled:${instanceId}`,
        title: "文件",
        path: "",
        name: "",
      });
    },

    openTerminalTab: (opts) => {
      const id = TEAM_TERMINAL_TAB_ID;
      const conversationId =
        useConversationStore.getState().currentConversationId;
      // User explicitly opened (or auto-surface after dismiss cleared) → allow future auto-surface.
      get().clearAutoSurfaceDismiss(terminalDismissKey(conversationId));

      const state = get();
      const terminals = state.tabs.filter(
        (t): t is TerminalDetailTab => t.kind === "terminal",
      );
      const activeTerminal = terminals.find((t) => t.id === state.activeTabId);
      const existing =
        terminals.find((t) => t.id === id) ?? activeTerminal ?? terminals[0];
      const activeWasTerminal = Boolean(activeTerminal);

      // Collapse legacy multi-instance terminal tabs into the singleton hub.
      if (terminals.length > 1 || (existing && existing.id !== id)) {
        set((s) => ({
          tabs: s.tabs.filter((t) => t.kind !== "terminal"),
          activeTabId: activeWasTerminal ? id : s.activeTabId,
        }));
      }

      const sessionId =
        opts?.sessionId !== undefined
          ? opts.sessionId
          : (existing?.sessionId ?? null);
      get().openTab(
        {
          kind: "terminal",
          id,
          title: opts?.title ?? "终端",
          sessionId,
        },
        {
          activate: opts?.activate !== false,
          reveal: opts?.reveal,
        },
      );
      return id;
    },

    bindTerminalSession: (_tabId, sessionId, title) => {
      set((s) => ({
        tabs: s.tabs.map((t) =>
          t.kind === "terminal"
            ? {
                ...t,
                id: TEAM_TERMINAL_TAB_ID,
                sessionId,
                title: title ?? t.title,
              }
            : t,
        ),
      }));
    },

    clearTerminalPreferredSession: (sessionId) => {
      set((s) => ({
        tabs: s.tabs.map((t) =>
          t.kind === "terminal" && t.sessionId === sessionId
            ? { ...t, sessionId: null }
            : t,
        ),
      }));
    },

    showBrowser: () => {
      const conversationId =
        useConversationStore.getState().currentConversationId;
      // User explicitly opened → allow future auto-surface.
      get().clearAutoSurfaceDismiss(browserDismissKey(conversationId));
      get().openTab({
        kind: "browser",
        id: TEAM_BROWSER_TAB_ID,
        title: "浏览器",
      });
      // 先/并 hydrate：有 server session 时激活该页（merge 优先 activeSessionId），
      // 勿让 ensureBlank 的本地空白抢激活；仅无 server 页时才补空白。
      if (!conversationId) {
        useBrowserSessionsStore.getState().ensureBlankPage(null);
        return;
      }
      void useBrowserSessionsStore
        .getState()
        .hydrateConversation(conversationId)
        .then(() => {
          const pages = useBrowserSessionsStore
            .getState()
            .pagesFor(conversationId);
          const hasServer = pages.some(
            (p) => p.serverSessionId != null && p.serverSessionId !== "",
          );
          if (!hasServer) {
            useBrowserSessionsStore.getState().ensureBlankPage(conversationId);
          }
        })
        .catch(() => {
          useBrowserSessionsStore.getState().ensureBlankPage(conversationId);
        });
    },
  };
}
