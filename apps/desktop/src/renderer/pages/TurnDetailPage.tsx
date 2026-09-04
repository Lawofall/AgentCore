import {
  ConversationHydrateOverlay,
  type ConversationHydratePhase,
} from "@/components/chat/ConversationHydrateOverlay";
import { TurnCompare } from "@/components/chat/compare/TurnCompare";
import { DebateArena } from "@/components/chat/debate/arena/DebateArena";
import { teamGraphVisible } from "@/components/chat/debatePreviewPlacement";
import { GraphView } from "@/components/graph/GraphView";
import {
  journalHydrateIdentity,
  journalHydrateIdentityEqual,
} from "@/components/graph/journalHydrate";
import { SidePanel } from "@/components/layout/SidePanel";
import { SidePanelToggle } from "@/components/layout/SidePanelToggle";
import { Button } from "@/components/ui";
import {
  ensureFullMessageRuns,
  fetchMessageWindow,
  shouldSetGeneratingOnHydrate,
} from "@/services/messages";
import { loadCachedConversation } from "@/services/offlineCache";
import { loadRecovery } from "@/services/resume";
import { scheduleHydrateAttachSettle } from "@/services/turns";
import {
  type MemoryUpdate,
  type Message,
  assistantProjectionId,
  getRuntime,
  useActiveMessages,
  useConversationStore,
} from "@/stores/conversation";
import {
  ExecutionScopeContext,
  hasRevisions,
  isDebate,
  useExecutionStore,
  useMessageExecution,
} from "@/stores/execution";
import { dismissFocusedFloat, useSidePanelStore } from "@/stores/sidePanel";
import type { TurnDetailView } from "@/stores/ui";
import {
  ArrowLeft,
  GitCompare,
  MessagesSquare,
  Network,
  Square,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { isDebateViewPending, resolveTurnDetailView } from "./turnDetailView";

function parseView(raw: string | null): TurnDetailView | null {
  if (raw === "graph" || raw === "debate" || raw === "compare") return raw;
  return null;
}

/**
 * Full-screen turn detail — graph / debate / compare for one turn.
 * Pure deep-read / replay surface (协作图与双视图UX.md §六 两个入口：聊天内嵌 ⇄ 全屏放大); no conversation-level
 * composer. Live turns only expose a top-bar Stop for the turn being viewed.
 */
export function TurnDetailPage() {
  const { id: conversationId, turnId } = useParams<{
    id: string;
    turnId: string;
  }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const [hydratePhase, setHydratePhase] =
    useState<ConversationHydratePhase>("ready");
  const [hydrateRetry, setHydrateRetry] = useState(0);

  const requestedView = parseView(searchParams.get("view"));
  const compareA = searchParams.get("a");
  const compareB = searchParams.get("b");
  const initialComparePair = useMemo<[string, string] | undefined>(() => {
    if (compareA && compareB) return [compareA, compareB];
    return undefined;
  }, [compareA, compareB]);

  // Ensure conversation data is loaded (same contract as ConversationPage:
  // early ready after adopt / SWR cache; attach/settle runs in background).
  // biome-ignore lint/correctness/useExhaustiveDependencies: hydrateRetry is an intentional re-run key
  useEffect(() => {
    if (!conversationId) return;
    const store = useConversationStore.getState();
    if (conversationId !== store.currentConversationId) {
      store.switchConversation(conversationId);
    }
    const recoveryLoaded = loadRecovery(conversationId);
    const warm =
      getRuntime(conversationId).messages.length > 0 ||
      getRuntime(conversationId).isGenerating;
    setHydratePhase(warm ? "ready" : "loading");
    let cancelled = false;
    void (async () => {
      const winPromise = fetchMessageWindow(conversationId);

      if (!warm) {
        const cached = await loadCachedConversation(conversationId);
        // Cache reveal is page-gated; do not return early — attach kick below
        // must still run after navigate-away.
        if (!cancelled && cached) {
          const s = useConversationStore.getState();
          if (s.currentConversationId === conversationId) {
            const rt = getRuntime(conversationId);
            if (!(rt.isGenerating || rt.messages.length > 0)) {
              s.setMessageWindow(
                cached.messages as Message[],
                {
                  hasMoreBefore: cached.hasMoreBefore,
                  hasMoreAfter: cached.hasMoreAfter,
                },
                conversationId,
              );
              s.setMemoryUpdates(
                cached.memoryUpdates as MemoryUpdate[],
                conversationId,
              );
              if (shouldSetGeneratingOnHydrate(cached.messages as Message[])) {
                s.setGenerating(true, conversationId);
              }
              setHydratePhase("ready");
            }
          }
        }
      }

      try {
        const win = await winPromise;
        // Adopt stays page-lifecycle gated; attach kick below does not.
        if (
          !cancelled &&
          useConversationStore.getState().currentConversationId ===
            conversationId
        ) {
          // Warm: keep slice. TurnDetail URL always targets a specific turn —
          // same product boundary as ConversationPage "has destination" (do not
          // snap to latest; that could unload the mid-history turn under view).
          // Cold: adopt empty or SWR-reconcile over cache.
          if (!warm) {
            const s = useConversationStore.getState();
            if (
              s.currentConversationId === conversationId &&
              !getRuntime(conversationId).isGenerating
            ) {
              s.setMessageWindow(
                win.messages,
                {
                  hasMoreBefore: win.hasMoreBefore,
                  hasMoreAfter: win.hasMoreAfter,
                },
                conversationId,
              );
              s.setMemoryUpdates(win.memoryUpdates, conversationId);
              if (shouldSetGeneratingOnHydrate(win.messages)) {
                s.setGenerating(true, conversationId);
              }
            }
          }
        }
        if (!cancelled) setHydratePhase("ready");
        scheduleHydrateAttachSettle(conversationId, recoveryLoaded);
      } catch {
        // Align with ConversationPage: offline cache, else explicit error (no silent blank).
        if (
          getRuntime(conversationId).messages.length > 0 ||
          getRuntime(conversationId).isGenerating
        ) {
          scheduleHydrateAttachSettle(conversationId, recoveryLoaded);
          if (!cancelled) setHydratePhase("ready");
        } else {
          const cached = await loadCachedConversation(conversationId);
          if (cached) {
            if (!cancelled) {
              const s = useConversationStore.getState();
              if (s.currentConversationId === conversationId) {
                const rt = getRuntime(conversationId);
                if (!(rt.isGenerating || rt.messages.length > 0)) {
                  s.setMessageWindow(
                    cached.messages as Message[],
                    {
                      hasMoreBefore: cached.hasMoreBefore,
                      hasMoreAfter: cached.hasMoreAfter,
                    },
                    conversationId,
                  );
                  s.setMemoryUpdates(
                    cached.memoryUpdates as MemoryUpdate[],
                    conversationId,
                  );
                }
              }
            }
            scheduleHydrateAttachSettle(conversationId, recoveryLoaded);
            if (!cancelled) setHydratePhase("ready");
          } else if (!warm) {
            scheduleHydrateAttachSettle(conversationId, recoveryLoaded);
            if (!cancelled) setHydratePhase("error");
          }
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId, hydrateRetry]);

  const [scopeId, setScopeId] = useState(turnId ?? "");
  useEffect(() => {
    if (turnId) setScopeId(turnId);
  }, [turnId]);

  const messages = useActiveMessages();

  const turnMessage = useMemo(
    () =>
      messages.find(
        (m) =>
          m.id === scopeId ||
          (m.role === "assistant" && assistantProjectionId(m) === scopeId),
      ),
    [messages, scopeId],
  );
  const scopeKey =
    turnMessage && turnMessage.role === "assistant"
      ? assistantProjectionId(turnMessage)
      : scopeId;

  // After a hydrate attempt, release debate-view pending even if journal had no plan.
  const [journalHydrateAttempted, setJournalHydrateAttempted] = useState(false);
  // biome-ignore lint/correctness/useExhaustiveDependencies: scopeKey/conversationId are intentional re-run keys
  useEffect(() => {
    setJournalHydrateAttempted(false);
  }, [scopeKey, conversationId]);

  const journal =
    turnMessage?.role === "assistant" && turnMessage.executionId
      ? turnMessage.runs
      : undefined;
  const journalIdentity = journalHydrateIdentity(journal);
  const journalIdentityRef = useRef(journalIdentity);
  if (
    !journalHydrateIdentityEqual(journalIdentityRef.current, journalIdentity)
  ) {
    journalIdentityRef.current = journalIdentity;
  }
  const stableJournalIdentity = journalIdentityRef.current;
  const hasTurnMessage = !!turnMessage;

  // Project the scoped turn's journal into execution store (cold deep-link /
  // refresh) — same gate as InlineTeamGraph: journal identity
  // (m.runs / events.length), not every turnMessage tick. Store applies
  // journalIsNewerThan (catch up after half-court; never roll live back).
  useEffect(() => {
    if (!scopeKey) return;
    if (!hasTurnMessage) {
      if (hydratePhase === "ready") setJournalHydrateAttempted(true);
      return;
    }
    if (conversationId && turnMessage?.runs?.eventsComplete === false) {
      void ensureFullMessageRuns(conversationId, scopeKey).then((got) => {
        if (!got) setJournalHydrateAttempted(true);
      });
      return;
    }
    if (stableJournalIdentity) {
      useExecutionStore
        .getState()
        .hydrateFromJournal(scopeKey, stableJournalIdentity.journal);
    }
    setJournalHydrateAttempted(true);
  }, [
    stableJournalIdentity,
    scopeKey,
    hydratePhase,
    hasTurnMessage,
    conversationId,
    turnMessage?.runs?.eventsComplete,
  ]);

  const execution = useMessageExecution(scopeKey);
  const showTeamGraph = teamGraphVisible(execution?.runs);
  const taskSummary = execution?.taskSummary;
  // Scoped to the turn being viewed — not "conversation is generating somewhere".
  const liveViewedTurn =
    messages.find(
      (m) =>
        m.id === scopeKey ||
        (m.role === "assistant" && assistantProjectionId(m) === scopeKey),
    )?.isStreaming ?? false;

  const debate = !!execution && isDebate(execution);
  // 对比 tab：按「是否存在可修订 run」判断，不按整图是否含辩论
  // （混合图幕 1 热修 + 幕 2 辩论时仍需对比入口）。
  const showCompare = !!execution && hasRevisions(execution);

  const hasJournalToProject = !!(
    turnMessage?.executionId &&
    turnMessage.runs &&
    !journalHydrateAttempted
  );
  const debateViewPending = isDebateViewPending({
    requestedView,
    debate,
    hydratePhase,
    hasJournalToProject,
    hasExecution: !!execution,
  });
  const overlayPhase: ConversationHydratePhase = debateViewPending
    ? "loading"
    : hydratePhase;

  const view: TurnDetailView = useMemo(() => {
    if (debateViewPending) return "graph"; // unused while pending (body gated)
    return resolveTurnDetailView({
      requestedView,
      debate,
      showCompare,
      execution,
    });
  }, [requestedView, debate, showCompare, execution, debateViewPending]);

  const setView = useCallback(
    (next: TurnDetailView) => {
      setSearchParams(
        (prev) => {
          const p = new URLSearchParams(prev);
          p.set("view", next);
          if (next !== "compare") {
            p.delete("a");
            p.delete("b");
          }
          return p;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const goBack = useCallback(() => {
    if (conversationId) navigate(`/conversations/${conversationId}`);
    else navigate(-1);
  }, [navigate, conversationId]);

  const stopGeneration = useCallback(() => {
    useConversationStore.getState().stopGeneration();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (dismissFocusedFloat()) {
        e.preventDefault();
        return;
      }
      const sp = useSidePanelStore.getState();
      const panelVisible =
        sp.open &&
        sp.tabs.some((t) => {
          if (t.id !== sp.activeTabId) return false;
          return (
            (t.kind === "run" ||
              t.kind === "content" ||
              t.kind === "simple-turn") &&
            t.messageId === scopeKey
          );
        });
      if (panelVisible) sp.closePanel();
      else goBack();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goBack, scopeKey]);

  useEffect(() => () => useSidePanelStore.getState().closeContentTabs(), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key === "i" || e.key === "I") {
        e.preventDefault();
        useSidePanelStore.getState().togglePanel();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // 右坞：打开后头栏 PanelRight 关闭；关闭时主区右上浮层打开。
  const panelOpen = useSidePanelStore((s) => s.open);

  if (!conversationId || !turnId) return null;

  return (
    <ExecutionScopeContext.Provider value={scopeKey}>
      <div className="relative flex h-full min-h-0 min-w-0 flex-1 flex-col bg-background">
        <div
          className={`flex h-12 shrink-0 items-center gap-3 border-b border-border px-4 ${!panelOpen ? "pr-14" : ""}`}
        >
          <Button
            variant="neutral"
            size="md"
            onClick={goBack}
            icon={<ArrowLeft size={16} />}
          >
            返回
          </Button>
          {taskSummary && (
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
              {taskSummary}
            </span>
          )}
          <div className="ml-auto flex shrink-0 items-center gap-2">
            {liveViewedTurn && (
              <Button
                variant="ghost"
                className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                icon={<Square size={14} />}
                onClick={stopGeneration}
                aria-label="停止生成"
              >
                停止
              </Button>
            )}
            <div className="flex shrink-0 items-center gap-0.5 rounded-lg border border-border bg-card p-0.5">
              <Button
                variant="ghost"
                onClick={() => setView("graph")}
                aria-pressed={!debateViewPending && view === "graph"}
                icon={<Network size={14} />}
                className={
                  !debateViewPending && view === "graph"
                    ? "bg-accent text-foreground hover:bg-accent"
                    : undefined
                }
              >
                协作图
              </Button>
              {debate && (
                <Button
                  variant="ghost"
                  onClick={() => setView("debate")}
                  aria-pressed={view === "debate"}
                  icon={<MessagesSquare size={14} />}
                  className={
                    view === "debate"
                      ? "bg-accent text-foreground hover:bg-accent"
                      : undefined
                  }
                >
                  辩论室
                </Button>
              )}
              {showCompare && (
                <Button
                  variant="ghost"
                  onClick={() => setView("compare")}
                  aria-pressed={view === "compare"}
                  icon={<GitCompare size={14} />}
                  className={
                    view === "compare"
                      ? "bg-accent text-foreground hover:bg-accent"
                      : undefined
                  }
                >
                  对比
                </Button>
              )}
            </div>
          </div>
        </div>

        {/* Body row: the content column (graph/debate/compare) and the
            right-docked SidePanel sit side-by-side in a flex-ROW, so the panel
            docks to the side instead of falling to the bottom of the page column
            (mirrors AppShell/ConversationPage, where SidePanel is a flex-row
            sibling — it is built as a `shrink-0 flex-col border-l` right dock). */}
        <div className="flex min-h-0 flex-1 overflow-hidden">
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
              {!debateViewPending && view === "graph" && (
                <div className="min-h-0 flex-1">
                  {showTeamGraph ? (
                    <GraphView interactive fitMode="view" />
                  ) : (
                    <div className="flex h-full w-full items-center justify-center">
                      <div className="text-center">
                        <p className="text-sm text-muted-foreground">
                          确认开工后再看协作图
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          回到对话点「开做」后，这里会展开团队进度
                        </p>
                      </div>
                    </div>
                  )}
                </div>
              )}
              {!debateViewPending &&
                view === "debate" &&
                debate &&
                execution && (
                  <div className="min-h-0 flex-1 overflow-y-auto p-4">
                    <DebateArena
                      execution={execution}
                      messageId={scopeKey}
                      conversationId={conversationId}
                      interactive={liveViewedTurn}
                    />
                  </div>
                )}
              {!debateViewPending &&
                view === "compare" &&
                showCompare &&
                execution && (
                  <div className="min-h-0 flex-1 overflow-y-auto p-4">
                    <div className="mx-auto max-w-5xl">
                      <TurnCompare
                        execution={execution}
                        messageId={scopeKey}
                        initialPair={initialComparePair}
                      />
                    </div>
                  </div>
                )}
              <ConversationHydrateOverlay
                phase={overlayPhase}
                onRetry={() => setHydrateRetry((n) => n + 1)}
              />
            </div>
          </div>

          <SidePanel />
        </div>

        {!panelOpen && (
          <div className="absolute right-3 top-2 z-20">
            <SidePanelToggle />
          </div>
        )}
      </div>
    </ExecutionScopeContext.Provider>
  );
}
