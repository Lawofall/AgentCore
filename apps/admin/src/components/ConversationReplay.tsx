import { ChatTimeline } from "@/components/conversation-replay/ChatTimeline";
import {
  InspectorPanel,
  type ReplaySessionMeta,
} from "@/components/conversation-replay/InspectorPanel";
import { ReplayComposerGhost } from "@/components/conversation-replay/ReplayComposerGhost";
import { ReplayOutline } from "@/components/conversation-replay/ReplayOutline";
import { Badge } from "@/components/ui/Badge";
import { ErrorState, TableSkeleton } from "@/components/ui/States";
import { isExecutionHarvestMessage } from "@/lib/executionHarvest";
import { foldEmptyAssistantFollowers } from "@/lib/foldEmptyAssistant";
import { cn, fmtCny, nanoToYuan } from "@/lib/utils";
import {
  type AdminConversationReplay,
  type AdminReplayTurnFinalState,
  type ReplayMessage,
  fetchConversationReplay,
  fetchReplayTurnFinalState,
} from "@/services/adminObservability";
import { errorMessage } from "@/services/api";
import { ArrowLeft, PanelRight } from "lucide-react";
import {
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useLocation, useSearchParams } from "react-router-dom";

/**
 * The turn being read is part of the address — `?turn=<message id>` — so an operator
 * can paste a link to one specific turn into a ticket and the next person lands on
 * that turn, reload included, instead of on the top of a forty-turn conversation.
 *
 * Not `useUrlFilters`, though the conventions it encodes still hold here: the default
 * (no anchor) stays out of the URL, an unresolvable id degrades to 未选中 rather than
 * throwing, and writes replace so that clicking through a dozen turns does not bury
 * the roster the operator came from under a dozen history entries. What that hook
 * cannot do is validate against data it has never seen — a turn id only means
 * something once this conversation has loaded — or carry router state across the
 * write, and `setSearchParams` navigates: a navigation without state drops the 来源页
 * that ReplayPage reads for 返回.
 */
const TURN_PARAM = "turn";

/** Worker dock: desktop side-panel default (400), never past half a laptop. */
const DOCK_MIN = 320;
const DOCK_MAX = 720;
const DOCK_DEFAULT = 400;
const DOCK_STEP = 24;
const DOCK_STORAGE_KEY = "admin:replay:dock-width";

function clampDock(px: number): number {
  return Math.min(DOCK_MAX, Math.max(DOCK_MIN, Math.round(px)));
}

function readDockWidth(): number {
  try {
    const raw = window.localStorage.getItem(DOCK_STORAGE_KEY);
    const parsed = raw == null ? Number.NaN : Number(raw);
    return Number.isFinite(parsed) ? clampDock(parsed) : DOCK_DEFAULT;
  } catch {
    return DOCK_DEFAULT;
  }
}

function persistDockWidth(px: number): void {
  try {
    window.localStorage.setItem(DOCK_STORAGE_KEY, String(px));
  } catch {
    // Private-mode / quota — the dock still resizes for this visit.
  }
}

const HYDRATE_CONCURRENCY = 2;

export function ConversationReplay({
  conversationId,
  onBack,
  backLabel = "返回观测",
}: {
  conversationId: string;
  onBack: () => void;
  backLabel?: string;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const anchorTrace = searchParams.get("trace") ?? undefined;
  const anchorTurn = searchParams.get(TURN_PARAM) ?? undefined;

  const [data, setData] = useState<AdminConversationReplay | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  /** Diagnose dock: 诊断 toggle or a team-graph node. Closed by default like desktop. */
  const [dockOpen, setDockOpen] = useState(false);
  const [dockWidth, setDockWidth] = useState(readDockWidth);
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const [finalById, setFinalById] = useState<
    Record<string, AdminReplayTurnFinalState>
  >({});
  const [hydratingIds, setHydratingIds] = useState<string[]>([]);
  const [hydrateError, setHydrateError] = useState<string | null>(null);
  const [hydrateNonce, setHydrateNonce] = useState(0);
  const finalByIdRef = useRef(finalById);
  finalByIdRef.current = finalById;
  const hydratePriorityIdRef = useRef<string | null>(null);
  const selectedIdForHydrateRef = useRef<string | null>(null);

  // RR hands back a fresh setter whenever the search string changes; holding both the
  // setter and the state to forward in refs keeps the writer identity stable.
  const setSearchParamsRef = useRef(setSearchParams);
  setSearchParamsRef.current = setSearchParams;
  const navStateRef = useRef(location.state);
  navStateRef.current = location.state;

  const writeTurnAnchor = useCallback((id: string) => {
    setSearchParamsRef.current(
      (prev) => {
        // Sibling params (a `trace` the operator arrived on) are somebody else's state.
        const next = new URLSearchParams(prev);
        next.set(TURN_PARAM, id);
        return next;
      },
      { replace: true, state: navStateRef.current },
    );
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchConversationReplay(conversationId));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [conversationId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setFinalById({});
    setHydratingIds([]);
    setHydrateError(null);
    setHydrateNonce(0);
  }, [conversationId]);

  const foldedTimeline = useMemo(() => {
    if (!data) {
      return {
        messages: [] as ReplayMessage[],
        shownIdFor: (id: string) => id,
      };
    }
    const merged = data.messages.map((m) => {
      const extra = finalById[m.id];
      if (!extra) return m;
      return {
        ...m,
        runs_payload: extra.runs_payload,
        projected: extra.projected,
      };
    });
    return foldEmptyAssistantFollowers(merged);
  }, [data, finalById]);

  const displayMessages = foldedTimeline.messages;

  const assistantTurns = useMemo(
    () => displayMessages.filter((m) => m.role === "assistant"),
    [displayMessages],
  );

  const multiAgentTurns = useMemo(
    () => assistantTurns.filter((m) => m.runs.length > 0).length,
    [assistantTurns],
  );

  const harvests = useMemo(
    () => (data?.messages ?? []).filter(isExecutionHarvestMessage),
    [data],
  );

  const outlineTurns = useMemo(
    () =>
      displayMessages
        .filter((m) => m.role === "user" && !isExecutionHarvestMessage(m))
        .map((m) => ({
          id: m.id,
          label: (m.content ?? "").trim().replace(/\s+/g, " ").slice(0, 80),
        })),
    [displayMessages],
  );

  /**
   * Selection is read back off the URL instead of being mirrored in state, so a pasted
   * link, a reload and 后退 cannot disagree about which turn is open. An anchor that no
   * longer resolves — hand-edited id, a turn from another conversation, history since
   * trimmed — leaves the page 未选中, which is the same place an anchor-less visit
   * starts from, rather than blank or thrown.
   */
  const selected = useMemo(() => {
    const messages = displayMessages;
    const byTurn = anchorTurn
      ? messages.find((m) => m.id === foldedTimeline.shownIdFor(anchorTurn))
      : undefined;
    if (byTurn) return byTurn;
    // 对话's 跳转 hands over a trace_id rather than a message id.
    const byTrace = anchorTrace
      ? messages.find((m) => m.trace_id === anchorTrace)
      : undefined;
    return byTrace ?? null;
  }, [displayMessages, foldedTimeline, anchorTrace, anchorTurn]);

  const selectedId = selected?.id ?? null;

  const hydratePriorityId =
    selected && selected.role === "assistant" && selected.has_final_state
      ? selected.id
      : null;
  hydratePriorityIdRef.current = hydratePriorityId;
  selectedIdForHydrateRef.current = selectedId;

  useEffect(() => {
    if (loading || !data || data.conversation.id !== conversationId) {
      return;
    }

    const pending = data.messages
      .filter(
        (m) =>
          m.role === "assistant" &&
          m.has_final_state &&
          !finalByIdRef.current[m.id],
      )
      .map((m) => m.id);
    if (pending.length === 0) {
      setHydratingIds((cur) => (cur.length === 0 ? cur : []));
      return;
    }

    const priority = hydratePriorityIdRef.current;
    const order = new Map(
      data.messages.map((m, index) => [m.id, index] as const),
    );
    const ordered = [...pending].sort((a, b) => {
      if (a === priority) return -1;
      if (b === priority) return 1;
      return (order.get(a) ?? 0) - (order.get(b) ?? 0);
    });

    let cancelled = false;
    let slots = 0;
    let nextIdx = 0;
    const started = new Set<string>();

    const pump = () => {
      while (
        !cancelled &&
        slots < HYDRATE_CONCURRENCY &&
        nextIdx < ordered.length
      ) {
        const id = ordered[nextIdx++];
        if (finalByIdRef.current[id] || started.has(id)) continue;
        started.add(id);
        slots += 1;
        if (id === selectedIdForHydrateRef.current) {
          setHydrateError(null);
        }
        setHydratingIds((cur) => (cur.includes(id) ? cur : [...cur, id]));
        void fetchReplayTurnFinalState(conversationId, id)
          .then((state) => {
            // Apply even if this generation was replaced (Strict Mode). Dropping
            // a successful body left the spinner on and the next effect with
            // nothing to fetch.
            setFinalById((prev) => ({ ...prev, [id]: state }));
          })
          .catch((err) => {
            if (cancelled) return;
            if (id === selectedIdForHydrateRef.current) {
              setHydrateError(errorMessage(err));
            }
          })
          .finally(() => {
            started.delete(id);
            setHydratingIds((cur) => cur.filter((item) => item !== id));
            if (cancelled) return;
            slots -= 1;
            pump();
          });
      }
    };

    pump();
    return () => {
      cancelled = true;
      if (started.size === 0) return;
      const abandoned = new Set(started);
      setHydratingIds((cur) => cur.filter((item) => !abandoned.has(item)));
    };
  }, [conversationId, data, loading, hydrateNonce]);

  const retryHydrate = useCallback(() => {
    if (!selectedId) return;
    setFinalById((prev) => {
      const next = { ...prev };
      delete next[selectedId];
      return next;
    });
    setHydrateError(null);
    setHydrateNonce((n) => n + 1);
  }, [selectedId]);

  const selectTurn = useCallback(
    (id: string) => {
      if (id !== selectedId) {
        setSelectedRunId(null);
      }
      writeTurnAnchor(id);
    },
    [selectedId, writeTurnAnchor],
  );

  const openDiagnose = useCallback(() => {
    if (!selectedId) {
      const last = assistantTurns.at(-1);
      if (last) writeTurnAnchor(last.id);
    }
    setDockOpen(true);
  }, [assistantTurns, selectedId, writeTurnAnchor]);

  /** Click graph node → open worker dock. */
  const selectRun = useCallback((runId: string) => {
    setSelectedRunId(runId);
    setDockOpen(true);
  }, []);

  const clearRun = useCallback(() => {
    setSelectedRunId(null);
  }, []);

  const closeDock = useCallback(() => {
    setSelectedRunId(null);
    setDockOpen(false);
  }, []);

  const startResize = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    dragRef.current = { startX: e.clientX, startWidth: dockWidth };
  };

  const moveResize = (e: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    // The dock is pinned right, so dragging the handle left makes it wider.
    setDockWidth(clampDock(drag.startWidth - (e.clientX - drag.startX)));
  };

  const endResize = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    persistDockWidth(dockWidth);
  };

  /**
   * A splitter that only answers to a mouse is a splitter half the operators can't
   * use, so the handle is a focusable `separator` driven by the arrow keys.
   */
  const keyResize = (e: KeyboardEvent<HTMLDivElement>) => {
    const next =
      e.key === "ArrowLeft"
        ? clampDock(dockWidth + DOCK_STEP)
        : e.key === "ArrowRight"
          ? clampDock(dockWidth - DOCK_STEP)
          : e.key === "Home"
            ? DOCK_MAX
            : e.key === "End"
              ? DOCK_MIN
              : null;
    if (next == null) return;
    e.preventDefault();
    setDockWidth(next);
    persistDockWidth(next);
  };

  const resetDockWidth = () => {
    setDockWidth(DOCK_DEFAULT);
    persistDockWidth(DOCK_DEFAULT);
  };

  /**
   * Where a trace jump landed, kept lit after the operator has moved on to another
   * turn. The turn anchor needs no such marker: it *is* the selection now.
   */
  const isAnchored = (m: ReplayMessage) =>
    anchorTrace != null && m.trace_id === anchorTrace;

  const sessionMeta: ReplaySessionMeta | null = data
    ? {
        title: data.conversation.title || "未命名会话",
        deleted: Boolean(data.conversation.deleted_at),
        userLabel: `${
          data.conversation.display_name ||
          data.conversation.username ||
          "未知用户"
        }${data.conversation.username ? ` @${data.conversation.username}` : ""}`,
        createdAt: data.conversation.created_at,
        conversationId: data.conversation.id,
        modelProfileName: data.conversation.model_profile_name,
        modelProfileId: data.conversation.model_profile_id,
        turns: data.turns,
        errors: data.errors,
        costLabel: fmtCny(nanoToYuan(data.cost_total)),
        multiAgentTurns,
      }
    : null;

  const dockCny =
    selected && selected.cost_total > 0 && data
      ? fmtCny(nanoToYuan(selected.cost_total))
      : null;

  const dockHarvest =
    selected && data ? precedingHarvest(displayMessages, selected) : null;

  const titleText = data?.conversation.title || "未命名会话";

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-start justify-between gap-3 px-3 pt-2">
        <div className="pointer-events-auto flex min-w-0 max-w-[70%] items-center gap-2 rounded-lg border border-border/60 bg-background/80 px-2 py-1 backdrop-blur">
          <ReplayBackButton onBack={onBack} backLabel={backLabel} />
          {!loading && !error && data && (
            <h1 className="flex min-w-0 items-center gap-1.5 truncate text-sm font-semibold text-foreground">
              <span className="truncate">{titleText}</span>
              {data.conversation.deleted_at ? (
                <Badge tone="neutral">会话已删</Badge>
              ) : null}
            </h1>
          )}
        </div>
        {!loading && !error && data && !dockOpen && (
          <button
            type="button"
            aria-label="打开诊断"
            title="诊断"
            onClick={openDiagnose}
            className="pointer-events-auto relative rounded-lg border border-border bg-card/80 p-2 text-muted-foreground outline-none backdrop-blur hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          >
            <PanelRight size={16} />
            {harvests.length > 0 && (
              <span className="absolute -right-1 -top-1 size-2 rounded-full bg-primary" />
            )}
          </button>
        )}
      </div>

      {loading && <TableSkeleton rows={6} columns={1} />}

      {!loading && error && (
        <ErrorState message={error} onRetry={() => void load()} />
      )}

      {!loading && !error && data && (
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          <div
            className={cn(
              "relative flex min-h-0 min-w-0 flex-1 flex-col",
              dockOpen && "hidden lg:flex",
            )}
          >
            <div
              aria-label="对话阅读区"
              className="min-h-0 flex-1 overflow-y-auto"
            >
              <ChatTimeline
                className="mx-auto w-full max-w-3xl px-6 pt-10 pb-4"
                messages={displayMessages}
                selectedId={selectedId}
                selectedRunId={selectedRunId}
                onSelect={selectTurn}
                onSelectRun={selectRun}
                isAnchored={isAnchored}
                hasMoreBefore={data.has_more_before}
                hydratingIds={hydratingIds}
                hydrateError={hydrateError}
                onRetryHydrate={retryHydrate}
              />
            </div>
            <ReplayComposerGhost />
            <ReplayOutline turns={outlineTurns} onJump={selectTurn} />
          </div>

          {dockOpen && (
            <>
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label="调整诊断面板宽度"
                aria-valuenow={dockWidth}
                aria-valuemin={DOCK_MIN}
                aria-valuemax={DOCK_MAX}
                tabIndex={0}
                onPointerDown={startResize}
                onPointerMove={moveResize}
                onPointerUp={endResize}
                onPointerCancel={endResize}
                onKeyDown={keyResize}
                onDoubleClick={resetDockWidth}
                title="拖拽调整宽度（双击复位）"
                className="group hidden w-3 shrink-0 cursor-col-resize touch-none items-center justify-center outline-none lg:flex"
              >
                <span
                  aria-hidden
                  className="h-10 w-px rounded-full bg-border transition-colors group-hover:bg-primary group-focus-visible:bg-primary"
                />
              </div>
              <div
                className="flex min-h-0 w-full flex-1 flex-col border-border border-l bg-background lg:w-[var(--dock-w)] lg:flex-none"
                style={{ "--dock-w": `${dockWidth}px` } as CSSProperties}
              >
                <InspectorPanel
                  className="min-h-0 flex-1 rounded-none border-0"
                  message={selected}
                  selectedRunId={selectedRunId}
                  onSelectRun={selectRun}
                  onClearRun={clearRun}
                  onClose={closeDock}
                  cnyLabel={dockCny}
                  harvest={dockHarvest}
                  harvests={harvests}
                  onSelectHarvest={(id) => {
                    selectTurn(id);
                    setDockOpen(true);
                  }}
                  session={sessionMeta}
                  hydrating={
                    Boolean(selected?.has_final_state) &&
                    selected != null &&
                    hydratingIds.includes(selected.id) &&
                    selected.runs_payload == null &&
                    selected.projected == null
                  }
                />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function precedingHarvest(
  messages: ReplayMessage[],
  selected: ReplayMessage,
): ReplayMessage | null {
  if (isExecutionHarvestMessage(selected)) return null;
  const idx = messages.findIndex((m) => m.id === selected.id);
  if (idx <= 0) return null;
  const prev = messages[idx - 1];
  return isExecutionHarvestMessage(prev) ? prev : null;
}

function ReplayBackButton({
  onBack,
  backLabel,
}: {
  onBack: () => void;
  backLabel: string;
}) {
  return (
    <button
      type="button"
      onClick={onBack}
      className="inline-flex shrink-0 items-center gap-1 rounded text-muted-foreground text-xs outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
    >
      <ArrowLeft size={14} aria-hidden />
      {backLabel}
    </button>
  );
}
