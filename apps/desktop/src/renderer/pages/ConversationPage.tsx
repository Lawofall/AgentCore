import { ChatView } from "@/components/chat/ChatView";
import {
  ConversationHydrateOverlay,
  type ConversationHydratePhase,
} from "@/components/chat/ConversationHydrateOverlay";
import { ConversationSplitView } from "@/components/chat/ConversationSplitView";
import { SidePanel } from "@/components/layout/SidePanel";
import { SidePanelToggle } from "@/components/layout/SidePanelToggle";
import {
  getConversations,
  useConversations,
  useGroupedConversationsSettled,
} from "@/hooks/useConversations";
import { ChatPaneProvider } from "@/lib/chatPane";
import {
  SPLIT_MIN_PANE_PX,
  dropMissingPanes,
  replaceFocusedPane,
} from "@/lib/conversationSplit";
import { logEvent } from "@/lib/log";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import {
  decideWarmOpenAction,
  fetchMessageWindow,
  jumpToMessage,
  loadLatestWindow,
  scheduleEnsureFullRunsForWindow,
  shouldSetGeneratingOnHydrate,
} from "@/services/messages";
import {
  loadCachedConversation,
  persistOpenedCache,
} from "@/services/offlineCache";
import { loadRecovery } from "@/services/resume";
import { clearLastEventId } from "@/services/streamConversation";
import {
  awaitHydrateAttachSettle,
  scheduleHydrateAttachSettle,
} from "@/services/turns";
import { syncConversationFollow } from "@/services/turns/conversationFollow";
import { hasLocalConversationStream } from "@/services/turns/streamOwnership";
import {
  type MemoryUpdate,
  type Message,
  getRuntime,
  hasUnconfirmedLocalTail,
  overlayCompleteRunsOnServerWindow,
  useConversationStore,
  windowHasSlimJournal,
} from "@/stores/conversation";
import { useConversationSplitStore } from "@/stores/conversationSplit";
import {
  WORKSPACE_TAB_ID,
  dismissFocusedFloat,
  useSidePanelStore,
} from "@/stores/sidePanel";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

function hasOpenDestination(conversationId: string): boolean {
  const pending = useConversationStore.getState().pendingFocus;
  return pending?.conversationId === conversationId;
}

function adoptMessageWindow(
  id: string,
  messages: Message[],
  flags: { hasMoreBefore: boolean; hasMoreAfter: boolean },
  memoryUpdates: MemoryUpdate[],
): boolean {
  if (messages.length === 0) return false;
  const s = useConversationStore.getState();
  if (s.currentConversationId !== id) return false;
  const rt = getRuntime(id);
  if (hasLocalConversationStream(id) || rt.messages.length > 0) return false;
  s.setMessageWindow(messages, flags, id);
  s.setMemoryUpdates(memoryUpdates, id);
  clearLastEventId(id);
  if (shouldSetGeneratingOnHydrate(messages)) {
    s.setGenerating(true, id);
  }
  return true;
}

/** Cold SWR: adopt the persisted server list over cache (never a live stream).
 * Empty GET must not wipe a revealed cache — that is not a server window. */
function reconcileMessageWindow(
  id: string,
  messages: Message[],
  flags: { hasMoreBefore: boolean; hasMoreAfter: boolean },
  memoryUpdates: MemoryUpdate[],
): boolean {
  const s = useConversationStore.getState();
  if (s.currentConversationId !== id) return false;
  if (hasLocalConversationStream(id)) return false;
  const existing = getRuntime(id).messages;
  if (messages.length === 0) {
    if (existing.length > 0) {
      logEvent("info", "conversation.slice_diag", {
        action: "cold_reconcile_reject_empty",
        conversation_id: id,
        before_count: existing.length,
        after_count: 0,
      });
    }
    return false;
  }
  const merged = overlayCompleteRunsOnServerWindow(messages, existing);
  s.setMessageWindow(merged, flags, id);
  s.setMemoryUpdates(memoryUpdates, id);
  clearLastEventId(id);
  if (shouldSetGeneratingOnHydrate(merged)) {
    s.setGenerating(true, id);
  }
  return true;
}

/** List metadata says this persisted conversation truly has zero messages. */
function isConfirmedEmptyConversation(id: string): boolean {
  return getConversations().find((c) => c.id === id)?.messageCount === 0;
}

function sliceHasVisibleContent(id: string): boolean {
  const rt = getRuntime(id);
  return rt.messages.length > 0 || hasLocalConversationStream(id);
}

export function ConversationPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const frameRef = useRef<HTMLDivElement>(null);
  // Cold open with `:id` must not paint one ready frame of empty draft before the
  // effect flips to loading (ConversationHydrateOverlay purpose). Draft `/` stays ready.
  const [hydratePhase, setHydratePhase] = useState<ConversationHydratePhase>(
    () => (id ? "loading" : "ready"),
  );
  const [hydrateRetry, setHydrateRetry] = useState(0);

  // 路由参数是 conversation 的真相来源（刷新/前进后退/直达链接时同步到 store），
  // 并从后端拉取最新一窗消息（含附件元信息）以恢复对话；更早的历史按需上滚加载。
  // biome-ignore lint/correctness/useExhaustiveDependencies: hydrateRetry is an intentional re-run key
  useEffect(() => {
    const store = useConversationStore.getState();
    // 索引路由 `/` = 新草稿：丢弃上一条已打开的会话，渲染空白对话。这样无论从哪个
    // 入口落到 `/`（导航「对话」、Ctrl/Cmd+N、刷新直达），看到的都是新对话，而不是
    // store 里残留的上次对话。draftWorkspaceIntent 不在这里碰——侧栏文件夹菜单、
    // 打开本机文件夹等入口已经写好落库目标（见 startNewConversation），清掉会丢掉。
    if (!id) {
      if (store.currentConversationId !== null) store.switchConversation(null);
      syncConversationFollow(null);
      setHydratePhase("ready");
      return;
    }
    if (id !== store.currentConversationId) store.switchConversation(id);

    // Load this conversation's recovery snapshot on reopen (recovery 统一, 对称 §8.2):
    // ONE owner-gated read that both (a) surfaces any turn paused at a plan_review /
    // ask_user checkpoint then disconnected (结构化挂起 2b) as a resume card above the
    // composer, and (b) reports whether a detached run is still live to 续看. Eager but
    // gated on reveal: overlay stays loading on an empty cold slice until recovery
    // projection lands, unless list metadata confirms messageCount===0. GET/cache with
    // content reveals immediately; recovery still runs in the background on that path.
    // After recovery, still-empty slice with messageCount!==0 → hydrate error (never
    // ready blank). Empty opened cache is not adopted for early reveal.
    // `loadRecovery` never rejects.
    const recoveryLoaded = loadRecovery(id);

    const warm =
      getRuntime(id).messages.length > 0 || hasLocalConversationStream(id);
    if (!warm) clearLastEventId(id);
    const warmRt = getRuntime(id);
    logEvent("info", "conversation.slice_diag", {
      action: "open_decide",
      conversation_id: id,
      warm,
      message_count: warmRt.messages.length,
      is_generating: warmRt.isGenerating,
      has_more_after: warmRt.hasMoreAfter,
      has_more_before: warmRt.hasMoreBefore,
    });

    let cancelled = false;
    const pageAc = new AbortController();
    const reveal = (): void => {
      if (cancelled) return;
      if (useConversationStore.getState().currentConversationId !== id) return;
      syncConversationFollow(id);
      setHydratePhase("ready");
    };
    const finishHydrateReveal = async (): Promise<void> => {
      if (cancelled) {
        scheduleHydrateAttachSettle(id, recoveryLoaded);
        return;
      }
      if (sliceHasVisibleContent(id) || isConfirmedEmptyConversation(id)) {
        reveal();
        scheduleHydrateAttachSettle(id, recoveryLoaded);
        return;
      }
      await awaitHydrateAttachSettle(id, recoveryLoaded);
      if (cancelled) {
        scheduleHydrateAttachSettle(id, recoveryLoaded);
        return;
      }
      if (sliceHasVisibleContent(id) || isConfirmedEmptyConversation(id)) {
        reveal();
        return;
      }
      // Cold slice still empty after recovery and list says messages exist — never
      // reveal a blank persisted conversation (hydrate error overlay covers slice).
      scheduleHydrateAttachSettle(id, recoveryLoaded);
      setHydratePhase("error");
    };
    if (warm) reveal();
    else setHydratePhase("loading");

    void (async () => {
      // Kick network early; online SWR may reveal from local cache first.
      const winPromise = fetchMessageWindow(id, {}, pageAc.signal);

      if (!warm) {
        const cached = await loadCachedConversation(id);
        // Cache reveal is page-gated; do not return early — attach kick below
        // must still run after navigate-away.
        if (
          !cancelled &&
          cached &&
          adoptMessageWindow(
            id,
            cached.messages as Message[],
            {
              hasMoreBefore: cached.hasMoreBefore,
              hasMoreAfter: cached.hasMoreAfter,
            },
            cached.memoryUpdates as MemoryUpdate[],
          )
        ) {
          logEvent("info", "conversation.hydrate", {
            conversation_id: id,
            branch: "online_swr_cache",
          });
          reveal();
        }
      }

      try {
        const win = await winPromise;
        // Adopt / reconcile stay page-lifecycle gated; attach kick below does not.
        if (
          !cancelled &&
          useConversationStore.getState().currentConversationId === id
        ) {
          // Cold: adopt empty or SWR-reconcile cache.
          // Warm: local stream / destination keep slice; idle no-destination → latest snap.
          if (!warm) {
            const wrote = reconcileMessageWindow(
              id,
              win.messages,
              {
                hasMoreBefore: win.hasMoreBefore,
                hasMoreAfter: win.hasMoreAfter,
              },
              win.memoryUpdates,
            );
            logEvent("info", "conversation.slice_diag", {
              action: "cold_reconcile",
              conversation_id: id,
              wrote,
              network_count: win.messages.length,
              has_more_after: win.hasMoreAfter,
            });
            if (wrote && win.messages.length > 0) {
              const merged = getRuntime(id).messages;
              if (!windowHasSlimJournal(merged)) {
                void persistOpenedCache(id, merged, win.memoryUpdates, {
                  hasMoreBefore: win.hasMoreBefore,
                  hasMoreAfter: win.hasMoreAfter,
                });
              }
              scheduleEnsureFullRunsForWindow(id);
            }
          } else {
            const rt = getRuntime(id);
            const action = decideWarmOpenAction({
              hasLocalStream: hasLocalConversationStream(id),
              hasDestination: hasOpenDestination(id),
              hasUnconfirmedTail: hasUnconfirmedLocalTail(rt.messages, {
                isGenerating: rt.isGenerating,
              }),
            });
            if (action === "snap_latest") {
              // Explicit snap (composer「跳到最新」同权) — crosses richer/hasMoreAfter.
              // persistOpenedCache runs inside loadLatestWindow on success (no double-write).
              const wrote = await loadLatestWindow(id, {
                signal: pageAc.signal,
              });
              logEvent("info", "conversation.slice_diag", {
                action: "warm_snap_latest",
                conversation_id: id,
                wrote,
                memory_count_before: rt.messages.length,
                memory_has_more_after_before: rt.hasMoreAfter,
              });
            } else {
              logEvent("info", "conversation.slice_diag", {
                action:
                  action === "skip_generating"
                    ? "warm_skip_reconcile"
                    : "warm_keep_anchor",
                conversation_id: id,
                reason: action,
                memory_count: rt.messages.length,
                network_count: win.messages.length,
                memory_has_more_after: rt.hasMoreAfter,
                network_has_more_after: win.hasMoreAfter,
                memory_tail_id: rt.messages.at(-1)?.id ?? null,
                network_tail_id: win.messages.at(-1)?.id ?? null,
              });
            }
          }
        }
        await finishHydrateReveal();
      } catch {
        if (cancelled) {
          scheduleHydrateAttachSettle(id, recoveryLoaded);
          return;
        }
        // N4-A: network / outage → fall back to local-store snapshot for this id.
        // Online SWR may already have revealed from cache — stay ready.
        if (
          getRuntime(id).messages.length > 0 ||
          hasLocalConversationStream(id) ||
          getRuntime(id).isGenerating
        ) {
          await finishHydrateReveal();
        } else {
          const cached = await loadCachedConversation(id);
          if (cached) {
            adoptMessageWindow(
              id,
              cached.messages as Message[],
              {
                hasMoreBefore: cached.hasMoreBefore,
                hasMoreAfter: cached.hasMoreAfter,
              },
              cached.memoryUpdates as MemoryUpdate[],
            );
            logEvent("info", "conversation.hydrate", {
              conversation_id: id,
              branch: "offline_cache",
            });
            await finishHydrateReveal();
          } else if (!warm) {
            scheduleHydrateAttachSettle(id, recoveryLoaded);
            // No cache + cold slice: explicit error (never silent blank like a draft).
            if (!cancelled) setHydratePhase("error");
            return;
          }
        }
      }
      // Honor a search-hit jump that navigated in from elsewhere: now that this
      // conversation's window is loaded, land on the hit (in-window → scroll;
      // outside → load-around). Runs after the load so it sees real messages.
      if (cancelled) return;
      const jumpStore = useConversationStore.getState();
      const pending = jumpStore.pendingFocus;
      if (pending && pending.conversationId === id) {
        jumpStore.clearPendingFocus();
        void jumpToMessage(id, pending.messageId, pageAc.signal);
      }
    })();
    return () => {
      cancelled = true;
      pageAc.abort();
      syncConversationFollow(null);
    };
  }, [id, hydrateRetry]);

  // 路由是焦点栏。单击、1–9、后退都先落到路由，再把分屏里的焦点栏换成它。
  useLayoutEffect(() => {
    const { split, setSplit } = useConversationSplitStore.getState();
    const next = replaceFocusedPane(split, id ?? null);
    if (next !== split) setSplit(next);
  }, [id]);

  const conversations = useConversations();
  const conversationsSettled = useGroupedConversationsSettled();
  useEffect(() => {
    if (!conversationsSettled) return;
    const known = new Set(conversations.map((c) => c.id));
    const { split, setSplit } = useConversationSplitStore.getState();
    const dropped = dropMissingPanes(split, (cid) => known.has(cid));
    if (dropped.split !== split) setSplit(dropped.split);
    if (dropped.navigateTo === "stay") return;
    if (dropped.navigateTo) navigate(`/conversations/${dropped.navigateTo}`);
    else navigate("/");
  }, [conversations, conversationsSettled, navigate]);

  useEffect(() => {
    const el = frameRef.current;
    if (!el) return;
    const apply = () => {
      useConversationSplitStore
        .getState()
        .setRoomFits(el.clientWidth >= SPLIT_MIN_PANE_PX * 2);
    };
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(el);
    return () => {
      observer.disconnect();
      useConversationSplitStore
        .getState()
        .setRoomFits(window.innerWidth >= 1280);
    };
  }, []);

  // Page-scoped shortcuts for the single side panel: Ctrl/Cmd+I shows / hides it
  // (keeping the active tab), Ctrl/Cmd+J reveals it straight on the 工作区 home
  // tab. Scoped here (not the global shell) as both are only meaningful on the
  // conversation page. (Ctrl/Cmd+B is reserved by the shell for the left sidebar
  // collapse, so the panel takes I to avoid the double-fire.)
  // 草稿（无 id）：右坞不可用 —— 快捷键不打开。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (dismissFocusedFloat()) {
          e.preventDefault();
        }
        return;
      }
      if (!(e.ctrlKey || e.metaKey)) return;
      if (!id) return;
      if (e.key === "i" || e.key === "I") {
        e.preventDefault();
        useSidePanelStore.getState().togglePanel();
      } else if (e.key === "j" || e.key === "J") {
        e.preventDefault();
        // Float focus: 钉回 first. Else smart toggle 工作区 / close dock.
        if (dismissFocusedFloat()) return;
        const s = useSidePanelStore.getState();
        if (s.open && s.activeTabId === WORKSPACE_TAB_ID) s.closePanel();
        else s.showWorkspace();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [id]);

  // 草稿强制关坞（含直达 `/`、persist 残留 open）；有会话后仍须用户手动开。
  useEffect(() => {
    if (!id) useSidePanelStore.getState().closePanel();
  }, [id]);

  // 对话页恒聊天。右坞：打开后头栏 PanelRight 关闭；关闭时主区右上浮层打开
  // （有会话时 Ctrl/Cmd+I；草稿不可用）。
  const panelOpen = useSidePanelStore((s) => s.open);
  const { isNarrow } = useNarrowLayoutState();
  const split = useConversationSplitStore((s) => s.split);
  const roomFits = useConversationSplitStore((s) => s.roomFits);
  const showSplit = Boolean(split) && roomFits && !isNarrow;
  const padDockToggle = Boolean(id && !panelOpen && !isNarrow);

  return (
    <>
      <div
        ref={frameRef}
        className="relative flex min-h-0 min-w-0 flex-1 flex-col"
      >
        {showSplit ? (
          <ConversationSplitView
            routeHydratePhase={hydratePhase}
            onRouteHydrateRetry={() => setHydrateRetry((n) => n + 1)}
            showDockToggle={padDockToggle}
          />
        ) : (
          <ChatPaneProvider id={id ?? null}>
            <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
              <ChatView />
              {id && (
                <ConversationHydrateOverlay
                  phase={hydratePhase}
                  onRetry={() => setHydrateRetry((n) => n + 1)}
                />
              )}
            </div>
          </ChatPaneProvider>
        )}
        {padDockToggle && !showSplit && (
          <div className="absolute right-3 top-2 z-20">
            <SidePanelToggle />
          </div>
        )}
      </div>
      {/* 草稿不挂右坞（不出现、不能打开）；有会话才挂载。 */}
      {id && <SidePanel />}
    </>
  );
}
