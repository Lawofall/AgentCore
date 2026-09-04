import { Markdown } from "@/components/chat/Markdown";
import { RunDetailScroll } from "@/components/chat/detail/RunDetailScroll";
import { FileTypeIcon } from "@/components/files/FileTypeIcon";
import { closeOsFloatWindowsForTabs } from "@/components/layout/DesktopFloatWindowBridge";
import { FileTabSurface } from "@/components/layout/FileTabSurface";
import { KillPtyConfirmDialog } from "@/components/terminal/KillPtyConfirmDialog";
import {
  TerminalPanelBody,
  useTerminalRegion,
} from "@/components/terminal/TerminalPanel";
import { Button, IconButton, TabChip } from "@/components/ui";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  HorizontalTabStrip,
  SortableTab,
  useSortableTabIds,
} from "@/components/ui/horizontal-tab-strip";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useBrowserRegion } from "@/components/workspace/BrowserLivePanel";
import { BrowserPanel } from "@/components/workspace/BrowserPanel";
import { ConversationChangesPanel } from "@/components/workspace/ConversationChangesPanel";
import { WorkspaceMode } from "@/components/workspace/WorkspacePanel";
import {
  shouldBounceChangesTabToWorkspace,
  shouldPinChangesTab,
} from "@/lib/conversationFileChanges";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { notifyError } from "@/lib/toast";
import { resolveConversationLocalTarget } from "@/services/sidecarRouting";
import {
  useActiveMessageContent,
  useConversationStore,
} from "@/stores/conversation";
import {
  type ExecutionRuntime,
  projectRuntime,
  useExecutionStore,
} from "@/stores/execution";
import {
  CHANGES_TAB_ID,
  type DetailTab,
  TEAM_BROWSER_TAB_ID,
  TEAM_TERMINAL_TAB_ID,
  WORKSPACE_TAB_ID,
  browserDismissKey,
  canFloatTabId,
  terminalDismissKey,
  useSidePanelStore,
} from "@/stores/sidePanel";
import {
  countBusyPtySessions,
  useUserTerminalStore,
} from "@/stores/userTerminals";
import {
  Diff,
  FileText,
  FolderOpen,
  MessageSquare,
  PanelRight,
  Plus,
  Radio,
  Sparkles,
  Terminal,
  UserRound,
} from "lucide-react";
import {
  type ReactNode,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

/**
 * Live-check for content tabs. Run / content tabs need a loaded execution plan (and a
 * run tab additionally needs its run in the PROJECTED execution — §9.3). Simple-turn
 * Q&A tabs have no plan; terminal / file / browser tabs are session references and
 * stay live while open. Simple-turn stays live while the conversation holds the answer.
 */
function isDetailTabLive(
  byId: Record<string, ExecutionRuntime>,
  tab: DetailTab,
): boolean {
  if (
    tab.kind === "simple-turn" ||
    tab.kind === "terminal" ||
    tab.kind === "file" ||
    tab.kind === "browser"
  ) {
    return true;
  }
  const rt = byId[tab.messageId];
  if (!rt?.plan) return false;
  if (tab.kind !== "run") return true;
  return projectRuntime(rt)?.runs.some((r) => r.id === tab.runId) ?? false;
}

/**
 * The conversation's single right-docked surface (前端UX设计.md §十 · 方案 B):
 * `[工作区*] [改动?] | 内容 tabs | [+]`.
 * 「工作区」常驻固定 tab：不可关、可 detach。「改动」按需打开、可关。
 */
export function SidePanel() {
  const { isNarrow } = useNarrowLayoutState();
  // 窄屏 / Capacitor 不上弹出（与 AppShell 浮窗宿主同一判断，勿引 isNativeRuntime）。
  const floatsDisabled = isNarrow || window.__NATIVE__ === true;
  const open = useSidePanelStore((s) => s.open);
  const width = useSidePanelStore((s) => s.width);
  const setWidth = useSidePanelStore((s) => s.setWidth);
  const reclampWidth = useSidePanelStore((s) => s.reclampWidth);
  const cycleWidth = useSidePanelStore((s) => s.cycleWidth);
  const closePanel = useSidePanelStore((s) => s.closePanel);
  const tabs = useSidePanelStore((s) => s.tabs);
  const floats = useSidePanelStore((s) => s.floats);
  const activeTabId = useSidePanelStore((s) => s.activeTabId);
  const setActiveTab = useSidePanelStore((s) => s.setActiveTab);
  const closeTab = useSidePanelStore((s) => s.closeTab);
  const reorderContentTabs = useSidePanelStore((s) => s.reorderContentTabs);
  const floatTab = useSidePanelStore((s) => s.floatTab);
  const clearFloats = useSidePanelStore((s) => s.clearFloats);
  const closeConversationScopedTabs = useSidePanelStore(
    (s) => s.closeConversationScopedTabs,
  );
  const openTerminalTab = useSidePanelStore((s) => s.openTerminalTab);
  const bindTerminalSession = useSidePanelStore((s) => s.bindTerminalSession);
  const openFileTab = useSidePanelStore((s) => s.openFileTab);
  const showBrowser = useSidePanelStore((s) => s.showBrowser);
  const showChanges = useSidePanelStore((s) => s.showChanges);
  const closeChanges = useSidePanelStore((s) => s.closeChanges);
  const changesOpen = useSidePanelStore((s) => s.changesOpen);
  const floatingIds = useMemo(
    () => new Set(floats.map((f) => f.tabId)),
    [floats],
  );
  const onPopOut = useCallback(
    (tabId: string) => {
      if (!floatTab(tabId)) {
        notifyError("浮窗已满（最多 8 个），请先钉回或关闭一个");
      }
    },
    [floatTab],
  );
  const currentConversationId = useConversationStore(
    (s) => s.currentConversationId,
  );
  const prevConversationIdRef = useRef<string | null | undefined>(undefined);
  const spawnSession = useUserTerminalStore((s) => s.spawnSession);

  // 流式性能 (白屏卡死修复·Stage 3 收窄订阅): gate this dock on the SET of live tabs, not on
  // the whole `byId`. Subscribing to `byId` re-ran the panel (tab strip + RunDetailBody) on
  // every streaming token; RunDetailBody self-subscribes to its own slot, so the shell only
  // needs to know WHICH tabs stay valid — a key that changes only when that set does.
  const liveTabKey = useExecutionStore((s) =>
    tabs
      .filter((t) => isDetailTabLive(s.byId, t))
      .map((t) => t.id)
      .join("\u0001"),
  );
  // 后台进程终端：活动出现时自动补一个 Terminal 内容 tab（不偷焦点），替代旧条件固定 tab。
  const terminal = useTerminalRegion();
  // 浏览器：活动出现时自动补一个 Browser 内容 tab（不偷焦点）。
  const browser = useBrowserRegion();

  const visibleTabs = useMemo(() => {
    const live = new Set(liveTabKey ? liveTabKey.split("\u0001") : []);
    // Move'd tabs leave the dock strip entirely (XOR dock/float).
    return tabs.filter((t) => live.has(t.id) && !floatingIds.has(t.id));
  }, [tabs, liveTabKey, floatingIds]);
  const visibleTabIds = useMemo(
    () => visibleTabs.map((t) => t.id),
    [visibleTabs],
  );
  const { getItemProps } = useSortableTabIds(visibleTabIds, reorderContentTabs);
  const activeTab =
    activeTabId === WORKSPACE_TAB_ID || activeTabId === CHANGES_TAB_ID
      ? null
      : (visibleTabs.find((t) => t.id === activeTabId) ?? null);
  const workspaceInDock = !floatingIds.has(WORKSPACE_TAB_ID);
  const changesPin = shouldPinChangesTab({
    conversationId: currentConversationId,
    changesOpen,
    isChangesFloating: floatingIds.has(CHANGES_TAB_ID),
  });
  const changesInDock = changesPin && !floatingIds.has(CHANGES_TAB_ID);
  const workspaceActive = workspaceInDock && activeTabId === WORKSPACE_TAB_ID;
  const changesActive = changesInDock && activeTabId === CHANGES_TAB_ID;

  // 切对话：卸改动 + 清浮窗 + 清对话作用域内容 tab；桌面须先/并关对应真窗。
  // closeChanges 只跟切对话走——初次挂载 / 进画布重挂不得卸掉用户刚打开的改动。
  useEffect(() => {
    const prevConversationId = prevConversationIdRef.current;
    prevConversationIdRef.current = currentConversationId;
    const floated = useSidePanelStore.getState().floats.map((f) => f.tabId);
    clearFloats();
    closeOsFloatWindowsForTabs(floated);
    closeConversationScopedTabs();
    const switched =
      prevConversationId !== undefined &&
      prevConversationId !== currentConversationId;
    if (!switched) return;
    closeChanges();
    const stillActive = useSidePanelStore.getState().activeTabId;
    if (
      shouldBounceChangesTabToWorkspace({
        activeTabId: stillActive,
        changesOpen: useSidePanelStore.getState().changesOpen,
      })
    ) {
      setActiveTab(WORKSPACE_TAB_ID);
    }
  }, [
    currentConversationId,
    closeChanges,
    clearFloats,
    closeConversationScopedTabs,
    setActiveTab,
  ]);

  // 终端「有活动」时自动补壳：用户关掉后记 dismiss，活动清零后才允许再次自动浮出。
  // 不含 canOpenPty——仅「能开 shell」不应强行挂 tab（否则关不掉）。
  useEffect(() => {
    const dismissKey = terminalDismissKey(currentConversationId);
    if (!terminal.show) {
      useSidePanelStore.getState().clearAutoSurfaceDismiss(dismissKey);
      return;
    }
    const sp = useSidePanelStore.getState();
    if (sp.isAutoSurfaceDismissed(dismissKey)) return;
    if (sp.tabs.some((t) => t.kind === "terminal")) return;
    openTerminalTab({ activate: false, reveal: false });
  }, [terminal.show, currentConversationId, openTerminalTab]);

  // 收敛历史多开终端顶栏 tab → 唯一壳（不激活、不强开）。
  useEffect(() => {
    const terminals = tabs.filter((t) => t.kind === "terminal");
    if (
      terminals.length <= 1 &&
      terminals.every((t) => t.id === TEAM_TERMINAL_TAB_ID)
    ) {
      return;
    }
    openTerminalTab({ activate: false, reveal: false });
  }, [tabs, openTerminalTab]);

  // 浏览器「有活动」时自动补壳：用户关掉后记 dismiss，活动清零后才允许再次自动浮出。
  useEffect(() => {
    const dismissKey = browserDismissKey(currentConversationId);
    if (!browser.show) {
      useSidePanelStore.getState().clearAutoSurfaceDismiss(dismissKey);
      return;
    }
    const sp = useSidePanelStore.getState();
    if (sp.isAutoSurfaceDismissed(dismissKey)) return;
    if (sp.tabs.some((t) => t.kind === "browser")) return;
    sp.openTab(
      { kind: "browser", id: TEAM_BROWSER_TAB_ID, title: "浏览器" },
      { activate: false, reveal: false },
    );
  }, [browser.show, currentConversationId]);

  // Content / simple-turn tabs read message text via narrow slices so a streaming
  // turn (a new `messages` array every tick) never re-renders this dock (收窄订阅).
  const contentMessageId =
    activeTab?.kind === "content" ? activeTab.contentMessageId : null;
  const contentTabText = useActiveMessageContent(contentMessageId);
  const simplePromptId =
    activeTab?.kind === "simple-turn" ? activeTab.promptMessageId : null;
  const simpleAnswerId =
    activeTab?.kind === "simple-turn" ? activeTab.answerMessageId : null;
  const simplePromptText = useActiveMessageContent(simplePromptId);
  const simpleAnswerText = useActiveMessageContent(simpleAnswerId);

  // Pay for the workspace / changes bodies' first fetch only once shown; keep
  // mounted afterwards so switching back is instant and state survives.
  const [wsMounted, setWsMounted] = useState(false);
  const [changesMounted, setChangesMounted] = useState(false);
  useEffect(() => {
    if (open && workspaceActive) setWsMounted(true);
  }, [open, workspaceActive]);
  useEffect(() => {
    if (open && changesActive) setChangesMounted(true);
  }, [open, changesActive]);

  // Keep-alive keys for terminal / file / run bodies (mounted while docked tab exists).
  const terminalTabs = useMemo(
    () =>
      visibleTabs.filter(
        (t): t is Extract<DetailTab, { kind: "terminal" }> =>
          t.kind === "terminal",
      ),
    [visibleTabs],
  );
  const fileTabs = useMemo(
    () =>
      visibleTabs.filter(
        (t): t is Extract<DetailTab, { kind: "file" }> => t.kind === "file",
      ),
    [visibleTabs],
  );
  const runTabs = useMemo(
    () =>
      visibleTabs.filter(
        (t): t is Extract<DetailTab, { kind: "run" }> => t.kind === "run",
      ),
    [visibleTabs],
  );
  const browserTabs = useMemo(
    () => visibleTabs.filter((t) => t.kind === "browser"),
    [visibleTabs],
  );

  useEffect(() => {
    reclampWidth();
    const onResize = () => reclampWidth();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [reclampWidth]);

  const onResizeStart = (e: ReactPointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = width;
    const onMove = (ev: PointerEvent) =>
      setWidth(startWidth + (startX - ev.clientX));
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const onNewTerminal = useCallback(async () => {
    const tabId = openTerminalTab();
    if (!currentConversationId) return;
    const target = await resolveConversationLocalTarget(currentConversationId);
    if (!target) return;
    const result = await spawnSession({
      conversationId: currentConversationId,
      rootId: target.rootId,
      subpath: target.subpath,
    });
    if (result.ok) {
      bindTerminalSession(tabId, result.session_id);
    }
  }, [
    openTerminalTab,
    currentConversationId,
    spawnSession,
    bindTerminalSession,
  ]);

  const [terminalCloseBusyCount, setTerminalCloseBusyCount] = useState<
    number | null
  >(null);
  const [terminalCloseBusy, setTerminalCloseBusy] = useState(false);

  const finishCloseTerminalTab = useCallback(async () => {
    setTerminalCloseBusy(true);
    try {
      if (currentConversationId) {
        const ok = await useUserTerminalStore
          .getState()
          .killConversation(currentConversationId);
        if (!ok) return;
      }
      setTerminalCloseBusyCount(null);
      closeTab(TEAM_TERMINAL_TAB_ID);
    } finally {
      setTerminalCloseBusy(false);
    }
  }, [currentConversationId, closeTab]);

  const onCloseContentTab = useCallback(
    (tabId: string) => {
      if (tabId !== TEAM_TERMINAL_TAB_ID) {
        closeTab(tabId);
        return;
      }
      const sessions = currentConversationId
        ? useUserTerminalStore.getState().sessionsFor(currentConversationId)
        : [];
      const busyCount = countBusyPtySessions(sessions);
      if (busyCount > 0) {
        setTerminalCloseBusyCount(busyCount);
        return;
      }
      void finishCloseTerminalTab();
    },
    [closeTab, currentConversationId, finishCloseTerminalTab],
  );

  if (!open) return null;

  return (
    <aside
      className={
        isNarrow
          ? "fixed inset-0 z-50 flex flex-col bg-card"
          : "relative flex shrink-0 flex-col border-l border-border bg-card"
      }
      style={isNarrow ? undefined : { width }}
      aria-modal={isNarrow || undefined}
      role={isNarrow ? "dialog" : undefined}
      aria-label={isNarrow ? "详情面板" : undefined}
    >
      {!isNarrow && (
        <Button
          variant="ghost"
          aria-label="拖拽调整面板宽度（双击在 最大/默认/最小 间复位）"
          onPointerDown={onResizeStart}
          onDoubleClick={cycleWidth}
          className="absolute left-0 top-0 z-10 h-full w-1 min-w-0 cursor-col-resize rounded-none bg-transparent p-0 hover:bg-primary/40"
        />
      )}

      <div className="flex h-11 min-h-0 shrink-0 items-center gap-1 overflow-hidden border-b border-border px-2 py-1.5 pr-1">
        <HorizontalTabStrip
          className="min-h-0 min-w-0 flex-1"
          contentClassName="gap-1"
          aria-label="侧面板标签"
        >
          {workspaceInDock && (
            <TabChip
              active={workspaceActive}
              onSelect={() => setActiveTab(WORKSPACE_TAB_ID)}
              onPopOut={
                floatsDisabled ? undefined : () => onPopOut(WORKSPACE_TAB_ID)
              }
              icon={<FolderOpen size={14} />}
              label="工作区"
            />
          )}
          {changesInDock && (
            <TabChip
              active={changesActive}
              onSelect={() => setActiveTab(CHANGES_TAB_ID)}
              onClose={closeChanges}
              onPopOut={
                floatsDisabled ? undefined : () => onPopOut(CHANGES_TAB_ID)
              }
              icon={<Diff size={14} />}
              label="改动"
            />
          )}
          <div className="mx-0.5 h-4 w-px shrink-0 bg-border" aria-hidden />
          {visibleTabs.map((tab) => (
            <SortableTab key={tab.id} id={tab.id} getItemProps={getItemProps}>
              <TabChip
                active={tab.id === activeTab?.id}
                icon={detailTabIcon(tab)}
                label={tab.title}
                onSelect={() => setActiveTab(tab.id)}
                onClose={() => onCloseContentTab(tab.id)}
                onPopOut={
                  !floatsDisabled && canFloatTabId(tab.id, tabs)
                    ? () => onPopOut(tab.id)
                    : undefined
                }
              />
            </SortableTab>
          ))}
        </HorizontalTabStrip>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <IconButton aria-label="新建标签" title="新建标签">
              <Plus size={15} />
            </IconButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-36">
            <DropdownMenuItem onSelect={() => openFileTab()}>
              <FileText size={14} />
              文件
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void onNewTerminal()}>
              <Terminal size={14} />
              终端
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => showBrowser()}>
              <Radio size={14} />
              浏览器
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => showChanges()}>
              <Diff size={14} />
              改动
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <SimpleTooltip label="隐藏侧面板 (Ctrl/Cmd+I)">
          <IconButton onClick={closePanel} aria-pressed aria-label="隐藏侧面板">
            <PanelRight size={15} />
          </IconButton>
        </SimpleTooltip>
      </div>

      <div className="relative min-h-0 flex-1">
        {wsMounted && workspaceInDock && (
          <div
            className={`absolute inset-0 ${workspaceActive ? "" : "hidden"}`}
          >
            <WorkspaceMode />
          </div>
        )}
        {changesMounted && changesInDock && (
          <div className={`absolute inset-0 ${changesActive ? "" : "hidden"}`}>
            <ConversationChangesPanel />
          </div>
        )}
        {terminalTabs.map((tab) => (
          <div
            key={tab.id}
            className={`absolute inset-0 ${
              activeTabId === tab.id ? "" : "hidden"
            }`}
          >
            <TerminalPanelBody preferredSessionId={tab.sessionId} />
          </div>
        ))}
        {fileTabs.map((tab) => (
          <div
            key={tab.id}
            className={`absolute inset-0 ${
              activeTabId === tab.id ? "" : "hidden"
            }`}
          >
            <FileTabSurface
              path={tab.path}
              name={tab.name}
              workspaceId={tab.workspaceId}
              channel={tab.channel}
              onClose={() => closeTab(tab.id)}
            />
          </div>
        ))}
        {browserTabs.map((tab) => {
          const active = activeTabId === tab.id;
          // 仅激活时挂载（切走即卸载 → 直播 SSE 随 BrowserLivePanel 断开）。
          if (!active) return null;
          return (
            <div key={tab.id} className="absolute inset-0">
              <BrowserPanel
                conversationId={browser.conversationId ?? currentConversationId}
                liveAvailable={browser.show}
              />
            </div>
          );
        })}
        {/* Multi-run keep-alive: every docked run stays mounted (hidden when inactive). */}
        {runTabs.map((tab) => (
          <div
            key={tab.id}
            className={`absolute inset-0 ${
              activeTabId === tab.id ? "" : "hidden"
            }`}
          >
            <RunDetailScroll
              key={`${tab.id}:${tab.runId}`}
              messageId={tab.messageId}
              runId={tab.runId}
            />
          </div>
        ))}
        {activeTab?.kind === "content" && (
          <div className="absolute inset-0 overflow-y-auto p-4">
            <Markdown content={contentTabText} />
          </div>
        )}
        {activeTab?.kind === "simple-turn" && (
          <div className="absolute inset-0 overflow-y-auto p-4">
            <section className="space-y-2">
              <h3 className="text-xs font-medium text-muted-foreground">
                提问
              </h3>
              <Markdown content={simplePromptText || "（无提问）"} />
            </section>
            <section className="mt-6 space-y-2 border-t border-border pt-6">
              <h3 className="text-xs font-medium text-muted-foreground">
                回答
              </h3>
              <Markdown
                content={
                  simpleAnswerText ||
                  (simpleAnswerId ? "（生成中…）" : "（无回答）")
                }
              />
            </section>
          </div>
        )}
      </div>
      <KillPtyConfirmDialog
        open={terminalCloseBusyCount != null}
        onOpenChange={(next) => {
          if (!next && !terminalCloseBusy) setTerminalCloseBusyCount(null);
        }}
        description={
          terminalCloseBusyCount != null && terminalCloseBusyCount > 1
            ? `关闭将终止 ${terminalCloseBusyCount} 个会话中的进程`
            : "关闭将终止此终端中的进程"
        }
        busy={terminalCloseBusy}
        onConfirm={() => void finishCloseTerminalTab()}
      />
    </aside>
  );
}

function detailTabIcon(tab: DetailTab): ReactNode {
  if (tab.kind === "content") {
    return tab.endpoint === "prompt" ? (
      <UserRound size={14} className="shrink-0" />
    ) : (
      <Sparkles size={14} className="shrink-0" />
    );
  }
  if (tab.kind === "simple-turn") {
    return <MessageSquare size={14} className="shrink-0" />;
  }
  if (tab.kind === "terminal") {
    return <Terminal size={14} className="shrink-0" />;
  }
  if (tab.kind === "file") {
    return <FileTypeIcon name={tab.name} path={tab.path} size={14} />;
  }
  if (tab.kind === "browser") {
    return <Radio size={14} className="shrink-0" />;
  }
  return null;
}

/** Closable content-tab chip (terminal / file / browser / run / endpoint / Q&A). */
export function RunTabChip({
  tab,
  active,
  onSelect,
  onClose,
}: {
  tab: DetailTab;
  active: boolean;
  onSelect: () => void;
  onClose: () => void;
}) {
  return (
    <TabChip
      active={active}
      icon={detailTabIcon(tab)}
      label={tab.title}
      onSelect={onSelect}
      onClose={onClose}
    />
  );
}
