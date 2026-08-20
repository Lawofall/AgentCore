import { ChatView } from "@/components/chat/ChatView";
import {
  ConversationHydrateOverlay,
  type ConversationHydratePhase,
} from "@/components/chat/ConversationHydrateOverlay";
import { SidePanel } from "@/components/layout/SidePanel";
import { SidePanelToggle } from "@/components/layout/SidePanelToggle";
import { logEvent } from "@/lib/log";
import {
  decideWarmOpenAction,
  fetchMessageWindow,
  jumpToMessage,
  loadLatestWindow,
  shouldSetGeneratingOnHydrate,
} from "@/services/messages";
import {
  loadCachedConversation,
  persistOpenedCache,
} from "@/services/offlineCache";
import { loadRecovery } from "@/services/resume";
import { clearLastEventId } from "@/services/streamConversation";
import { scheduleHydrateAttachSettle } from "@/services/turns";
import { syncConversationFollow } from "@/services/turns/conversationFollow";
import { hasLocalConversationStream } from "@/services/turns/streamOwnership";
import { useBookmarkStore } from "@/stores/bookmarks";
import {
  type MemoryUpdate,
  type Message,
  getRuntime,
  isMessageWindowStrictlyRicher,
  useConversationStore,
} from "@/stores/conversation";
import {
  WORKSPACE_TAB_ID,
  dismissFocusedFloat,
  useSidePanelStore,
} from "@/stores/sidePanel";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

/** Read the `msg` query of the current hash route (#/conversations/:id?msg=<id>).
 * Parsed off window.location so the load effect need not depend on router search state. */
function readMsgAnchor(): string | null {
  const hash = window.location.hash;
  const q = hash.indexOf("?");
  if (q === -1) return null;
  return new URLSearchParams(hash.slice(q + 1)).get("msg");
}

function hasOpenDestination(conversationId: string): boolean {
  const pending = useConversationStore.getState().pendingFocus;
  if (pending?.conversationId === conversationId) return true;
  return readMsgAnchor() != null;
}

function adoptMessageWindow(
  id: string,
  messages: Message[],
  flags: { hasMoreBefore: boolean; hasMoreAfter: boolean },
  memoryUpdates: MemoryUpdate[],
): boolean {
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

/** Cold SWR: overwrite empty/cache-backed slice from network (never a live stream).
 * Same richer-dominance as {@link loadLatestWindow}: a thinner GET (in-flight
 * journal not flushed / harvest without `run_plan`) must not wipe a cache that
 * already has the team marker + journal — that is the refresh flash-then-gone. */
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
  if (
    existing.length > 0 &&
    !isMessageWindowStrictlyRicher(messages, existing)
  ) {
    logEvent("info", "conversation.slice_diag", {
      action: "cold_reconcile_reject_not_richer",
      conversation_id: id,
      before_count: existing.length,
      after_count: messages.length,
    });
    return false;
  }
  s.setMessageWindow(messages, flags, id);
  s.setMemoryUpdates(memoryUpdates, id);
  clearLastEventId(id);
  if (shouldSetGeneratingOnHydrate(messages)) {
    s.setGenerating(true, id);
  }
  return true;
}

export function ConversationPage() {
  const { id } = useParams<{ id: string }>();
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
    // store 里残留的上次对话。draftWorkspaceIntent 不在这里碰——那是「新建对话」入口
    // 设置的落库目标（见 startNewConversation），清掉会破坏「全部对话」按项目新建。
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
    // non-blocking: overlay reveals after the message window (or cache), then
    // `scheduleHydrateAttachSettle` runs when this promise lands. `loadRecovery` never
    // rejects, so the handle is safe to leave unawaited on the paths that skip the gate.
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
            if (wrote) {
              void persistOpenedCache(id, win.messages, win.memoryUpdates, {
                hasMoreBefore: win.hasMoreBefore,
                hasMoreAfter: win.hasMoreAfter,
              });
            }
          } else {
            const rt = getRuntime(id);
            const action = decideWarmOpenAction({
              hasLocalStream: hasLocalConversationStream(id),
              hasDestination: hasOpenDestination(id),
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
              logEvent("warn", "conversation.slice_diag", {
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
        // Reveal as soon as the window is in the store. Recovery/attach stay
        // eager in the background — they must not cover already-adopted text.
        reveal();
        scheduleHydrateAttachSettle(id, recoveryLoaded);
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
          scheduleHydrateAttachSettle(id, recoveryLoaded);
          reveal();
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
            scheduleHydrateAttachSettle(id, recoveryLoaded);
            reveal();
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
      } else {
        // 消息永久链接 (对话基础功能补齐): a #/conversations/:id?msg=<messageId> anchor
        // (from「复制消息链接」or the web build) lands on the exact turn. Read the hash
        // query imperatively so the load effect stays keyed on [id] alone — re-parsing
        // via useSearchParams would fold URL churn into the deps and re-fetch the window.
        const target = readMsgAnchor();
        if (target) void jumpToMessage(id, target, pageAc.signal);
      }
    })();
    return () => {
      cancelled = true;
      pageAc.abort();
      syncConversationFollow(null);
    };
  }, [id, hydrateRetry]);

  // 消息收藏 star state (方向 4): load which of this conversation's messages are
  // bookmarked so their bubbles render a filled star. Best-effort + independent of
  // the history load (a failed fetch just leaves stars empty).
  useEffect(() => {
    if (!id) return;
    void useBookmarkStore.getState().hydrateForConversation(id);
  }, [id]);

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

  return (
    <>
      <ChatView />
      {id && (
        <ConversationHydrateOverlay
          phase={hydratePhase}
          onRetry={() => setHydrateRetry((n) => n + 1)}
        />
      )}
      {id && !panelOpen && (
        <div className="absolute right-3 top-2 z-20">
          <SidePanelToggle />
        </div>
      )}
      {/* 草稿不挂右坞（不出现、不能打开）；有会话才挂载。 */}
      {id && <SidePanel />}
    </>
  );
}
