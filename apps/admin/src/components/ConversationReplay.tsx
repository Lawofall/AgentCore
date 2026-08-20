import { CopyableId } from "@/components/CopyableId";
import { ChatTimeline } from "@/components/conversation-replay/ChatTimeline";
import { InspectorPanel } from "@/components/conversation-replay/InspectorPanel";
import { TurnOpsBar } from "@/components/conversation-replay/TurnOpsBar";
import { Badge } from "@/components/ui/Badge";
import { Page } from "@/components/ui/Page";
import { ErrorState, TableSkeleton } from "@/components/ui/States";
import { isExecutionHarvestMessage } from "@/lib/executionHarvest";
import { cn, fmtCny, fmtInt, fmtTime, nanoToYuan } from "@/lib/utils";
import {
  type AdminConversationReplay,
  type AdminReplayTurnFinalState,
  type ReplayMessage,
  fetchConversationReplay,
  fetchReplayTurnFinalState,
} from "@/services/adminObservability";
import { errorMessage } from "@/services/api";
import { ArrowLeft, Users } from "lucide-react";
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

/** Worker dock width bounds, in px. Wide enough for prose, never past half a laptop. */
const DOCK_MIN = 320;
const DOCK_MAX = 720;
const DOCK_DEFAULT = 480;
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
  /** Right dock only opens when a worker/node is selected — no standalone 检视入口. */
  const [dockOpen, setDockOpen] = useState(false);
  const [dockWidth, setDockWidth] = useState(readDockWidth);
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const [finalById, setFinalById] = useState<
    Record<string, AdminReplayTurnFinalState>
  >({});
  const [hydratingId, setHydratingId] = useState<string | null>(null);
  const [hydrateError, setHydrateError] = useState<string | null>(null);
  const [hydrateNonce, setHydrateNonce] = useState(0);

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
    setHydratingId(null);
    setHydrateError(null);
    setHydrateNonce(0);
  }, [conversationId]);

  const displayMessages = useMemo(() => {
    if (!data) return [];
    return data.messages.map((m) => {
      const extra = finalById[m.id];
      if (!extra) return m;
      return {
        ...m,
        runs_payload: extra.runs_payload,
        projected: extra.projected,
      };
    });
  }, [data, finalById]);

  const assistantTurns = useMemo(
    () => (data?.messages ?? []).filter((m) => m.role === "assistant"),
    [data],
  );

  const multiAgentTurns = useMemo(
    () => assistantTurns.filter((m) => m.runs.length > 0).length,
    [assistantTurns],
  );

  const harvests = useMemo(
    () => (data?.messages ?? []).filter(isExecutionHarvestMessage),
    [data],
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
      ? messages.find((m) => m.id === anchorTurn)
      : undefined;
    if (byTurn) return byTurn;
    // 对话's 跳转 hands over a trace_id rather than a message id.
    const byTrace = anchorTrace
      ? messages.find((m) => m.trace_id === anchorTrace)
      : undefined;
    return byTrace ?? null;
  }, [displayMessages, anchorTrace, anchorTurn]);

  const selectedId = selected?.id ?? null;

  useEffect(() => {
    if (
      loading ||
      !data ||
      data.conversation.id !== conversationId ||
      !selected ||
      selected.role !== "assistant" ||
      !selected.has_final_state ||
      finalById[selected.id]
    ) {
      return;
    }
    const id = selected.id;
    let cancelled = false;
    setHydratingId(id);
    setHydrateError(null);
    void fetchReplayTurnFinalState(conversationId, id)
      .then((state) => {
        if (cancelled) return;
        setFinalById((prev) => ({ ...prev, [id]: state }));
      })
      .catch((err) => {
        if (cancelled) return;
        setHydrateError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) {
          setHydratingId((cur) => (cur === id ? null : cur));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [
    conversationId,
    data,
    loading,
    selected,
    finalById,
    hydrateNonce,
  ]);

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
      // The open worker belongs to the turn being left behind.
      if (id !== selectedId) {
        setSelectedRunId(null);
        setDockOpen(false);
      }
      writeTurnAnchor(id);
    },
    [selectedId, writeTurnAnchor],
  );

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

  const dockCny =
    selected && selected.cost_total > 0 && data
      ? fmtCny(nanoToYuan(selected.cost_total))
      : null;

  const dockHarvest =
    selected && data
      ? precedingHarvest(displayMessages, selected)
      : null;

  /**
   * The turn the dock is showing, and the single condition behind both the dock and
   * the timeline's narrow-screen hiding. Editing the anchor out of the address bar
   * while the dock is open would otherwise hide the timeline with nothing in its
   * place — a blank page for a URL that is merely stale.
   */
  const dockMessage = dockOpen ? selected : null;

  return (
    <Page className="flex h-full min-h-0 flex-col px-4 py-3 lg:px-6">
      {/*
        Session strip, not PageHeader: that component's mb-6 / gap-4 stack is
        title + actions + filters as three layers. The back control stays
        mounted across load so a click cannot land on a just-unmounted node.
      */}
      <header className="mb-2 shrink-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <ReplayBackButton onBack={onBack} backLabel={backLabel} />
          {!loading && !error && data && (
            <>
              <h1 className="inline-flex min-w-0 items-center gap-1.5 text-sm font-semibold text-foreground">
                <span>{data.conversation.title || "未命名会话"}</span>
                {data.conversation.deleted_at ? (
                  <Badge tone="neutral">会话已删</Badge>
                ) : null}
              </h1>
              <span className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-muted-foreground text-xs">
                <span>
                  {data.conversation.display_name ||
                    data.conversation.username ||
                    "未知用户"}
                  {data.conversation.username && (
                    <span> @{data.conversation.username}</span>
                  )}
                </span>
                <span aria-hidden>·</span>
                <span className="tabular-nums">
                  {fmtTime(data.conversation.created_at)}
                </span>
                <span aria-hidden>·</span>
                <CopyableId
                  value={data.conversation.id}
                  label="conversation_id"
                  display={data.conversation.id.slice(0, 8)}
                />
                {data.conversation.model_profile_name && (
                  <>
                    <span aria-hidden>·</span>
                    <span
                      title={
                        data.conversation.model_profile_id
                          ? `profile ${data.conversation.model_profile_id}`
                          : undefined
                      }
                    >
                      {data.conversation.model_profile_name}
                      {data.conversation.model_profile_id && (
                        <span className="ml-1 font-mono text-xs">
                          {data.conversation.model_profile_id.slice(0, 8)}
                        </span>
                      )}
                    </span>
                  </>
                )}
              </span>
              <div className="flex flex-wrap items-center gap-1 text-xs">
                <KpiChip label="回合" value={fmtInt(data.turns)} />
                <KpiChip
                  label="错误"
                  value={fmtInt(data.errors)}
                  tone={data.errors > 0 ? "destructive" : undefined}
                />
                <KpiChip
                  label="成本"
                  value={fmtCny(nanoToYuan(data.cost_total))}
                />
                {multiAgentTurns > 0 && (
                  <KpiChip
                    label="多 Agent"
                    value={`${multiAgentTurns} 回合`}
                    tone="primary"
                  />
                )}
              </div>
            </>
          )}
        </div>
        {!loading && !error && data && (
          <>
            <TurnPills
              turns={assistantTurns}
              selectedId={selectedId}
              onSelect={selectTurn}
              anchorTrace={anchorTrace}
            />
            <TurnOpsBar
              selected={selected}
              harvests={harvests}
              onSelectHarvest={(id) => {
                selectTurn(id);
                setDockOpen(true);
              }}
              onOpenDock={() => setDockOpen(true)}
            />
          </>
        )}
      </header>

      {loading && <TableSkeleton rows={6} columns={1} />}

      {!loading && error && (
        <ErrorState message={error} onRetry={() => void load()} />
      )}

      {!loading && !error && data && (
        <>
          {/*
            One timeline and one dock, laid out by the shell's height rather than by a
            `calc(100vh - …)` guess: the row fills whatever the scroll container gives
            it and each pane scrolls on its own. Narrow screens stack, and an open dock
            takes the whole width by hiding the timeline — hiding, not unmounting, so
            the reading position survives a trip into a worker and back.
          */}
          <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row lg:gap-0">
            <ChatTimeline
              className={cn("min-w-0 flex-1", dockMessage && "hidden lg:flex")}
              messages={displayMessages}
              selectedId={selectedId}
              selectedRunId={selectedRunId}
              onSelect={selectTurn}
              onSelectRun={selectRun}
              isAnchored={isAnchored}
              hasMoreBefore={data.has_more_before}
              hydratingId={hydratingId}
              hydrateError={hydrateError}
              onRetryHydrate={retryHydrate}
            />

            {dockMessage && (
              <>
                <div
                  role="separator"
                  aria-orientation="vertical"
                  aria-label="调整队员面板宽度"
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
                  className="flex min-h-0 w-full flex-1 flex-col lg:w-[var(--dock-w)] lg:flex-none"
                  style={{ "--dock-w": `${dockWidth}px` } as CSSProperties}
                >
                  <InspectorPanel
                    className="min-h-0 flex-1"
                    message={dockMessage}
                    selectedRunId={selectedRunId}
                    onSelectRun={selectRun}
                    onClearRun={clearRun}
                    onClose={closeDock}
                    cnyLabel={dockCny}
                    harvest={dockHarvest}
                  />
                </div>
              </>
            )}
          </div>
        </>
      )}
    </Page>
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

function KpiChip({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "destructive" | "primary";
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border border-border bg-muted/40 px-1.5 py-0.5 tabular-nums",
        tone === "destructive" && "border-destructive/30 text-destructive",
        tone === "primary" && "border-primary/30 text-primary",
      )}
    >
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-foreground">{value}</span>
    </span>
  );
}

/**
 * Turn index. Scrolls sideways instead of wrapping: a 40-turn conversation used to
 * push the timeline off the bottom of the window before it had rendered a line.
 */
function TurnPills({
  turns,
  selectedId,
  onSelect,
  anchorTrace,
}: {
  turns: ReplayMessage[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  anchorTrace?: string;
}) {
  if (turns.length === 0) {
    return (
      <p className="mt-1.5 text-muted-foreground text-xs">暂无助手回合</p>
    );
  }

  return (
    <div className="mt-1.5 flex w-full min-w-0 items-center gap-1.5 overflow-x-auto pb-1">
      <span className="shrink-0 text-muted-foreground text-xs font-medium">
        回合
      </span>
      {turns.map((m, i) => {
        const isError = m.metrics?.status === "error";
        const multi = m.runs.length > 0 || m.metrics?.delegated;
        const anchored = anchorTrace != null && m.trace_id === anchorTrace;
        const active = selectedId === m.id;
        return (
          <button
            key={m.id}
            type="button"
            // Selection is a ring and a tint otherwise — invisible to a screen reader,
            // and the only handle a test has on "this link opened that turn".
            aria-current={active ? "true" : undefined}
            onClick={() => onSelect(m.id)}
            className={cn(
              "inline-flex shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1 text-left text-xs outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
              active
                ? "border-primary/40 bg-primary/10 text-foreground"
                : "border-border bg-card text-muted-foreground hover:bg-muted/50 hover:text-foreground",
              anchored && !active && "ring-1 ring-primary/40",
            )}
          >
            <span className="font-medium tabular-nums">#{i + 1}</span>
            {/* The pill already separates 回合号 from time by weight — dimming the
                timestamp on top of that read at 2.7:1. */}
            <span className="tabular-nums">{fmtTime(m.created_at)}</span>
            {(isError || multi) && (
              <span className="flex items-center gap-1">
                {isError && <Badge tone="destructive">错</Badge>}
                {multi && (
                  <Badge tone="primary">
                    <Users size={10} className="mr-0.5" />
                    {m.metrics?.workers || m.runs.length || "多"}
                  </Badge>
                )}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
