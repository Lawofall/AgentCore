import { AssistantContent } from "@/components/AssistantView";
import { FileArtifactsCard } from "@/components/FileArtifactsCard";
import { HangingQuestionBar } from "@/components/HangingQuestionBar";
import { resolveFileArtifactsForCard } from "@/lib/fileArtifacts";
import { PREVIEW_FIXTURES } from "@/preview/fixtures";
import {
  extractAsks,
  extractCoordinationWait,
  extractEscalationSlots,
  extractEvidenceLedger,
  extractExecutionDetached,
  extractGraphAppendActKinds,
  extractGraphAppendAuthorizedBy,
  extractHotDecisionTraces,
  extractPrevExecutionIds,
  extractRunToolCalls,
  extractStageCardTraces,
  extractToolPhases,
  extractWorkerToolPhases,
  fold,
} from "@/protocol/fold";
import { extractTeamPreviewTraces } from "@/protocol/teamPreviewTraces";
import type { SSEEvent } from "@agentcore/contract-types";
import { turnElapsedMs } from "@agentcore/protocol-fold-kit";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";

function eventsForFrame(events: SSEEvent[], frame: number | null): SSEEvent[] {
  if (frame === null) return events;
  return events.slice(0, Math.max(0, Math.min(frame, events.length)));
}

/**
 * Hidden dev route (`/preview`) — replays conformance vectors through the real mobile fold +
 * {@link AssistantContent}. Zero backend, zero LLM. Reach by URL only; not in the tab bar.
 */
export function PreviewPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  const requested = searchParams.get("s");
  const current =
    PREVIEW_FIXTURES.find((f) => f.name === requested) ??
    PREVIEW_FIXTURES[0] ??
    null;

  const frameRaw = searchParams.get("k");
  const parsedFrame =
    frameRaw === null ? Number.NaN : Number.parseInt(frameRaw, 10);
  const frame =
    Number.isFinite(parsedFrame) && parsedFrame > 0 ? parsedFrame : null;

  const events = useMemo(
    () => (current ? eventsForFrame(current.events, frame) : []),
    [current, frame],
  );

  const projected = useMemo(() => fold(events), [events]);
  const asks = useMemo(() => extractAsks(events), [events]);
  const escalationSlots = useMemo(
    () => extractEscalationSlots(events),
    [events],
  );
  const hotTraces = useMemo(() => extractHotDecisionTraces(events), [events]);
  const stageCardTraces = useMemo(
    () => extractStageCardTraces(events),
    [events],
  );
  const teamPreviewTraces = useMemo(
    () => extractTeamPreviewTraces(events),
    [events],
  );
  const toolPhases = useMemo(() => extractToolPhases(events), [events]);
  const workerToolPhases = useMemo(
    () => extractWorkerToolPhases(events),
    [events],
  );
  const pendingEscalations = useMemo(() => {
    const map = new Map<string, string>();
    for (const i of projected.interactions) {
      if (i.kind === "escalation" && i.status === "pending") {
        map.set(i.runId, i.id);
      }
    }
    return map;
  }, [projected.interactions]);
  const runToolCalls = useMemo(() => extractRunToolCalls(events), [events]);
  const waitProgress = useMemo(() => extractCoordinationWait(events), [events]);
  const detached = useMemo(() => extractExecutionDetached(events), [events]);
  const debateEvidenceLedger = useMemo(
    () => extractEvidenceLedger(events),
    [events],
  );
  const graphAppendActKinds = useMemo(
    () => extractGraphAppendActKinds(events),
    [events],
  );
  const graphAppendAuthorizedBy = useMemo(
    () => extractGraphAppendAuthorizedBy(events),
    [events],
  );
  const prevExecutionIds = useMemo(
    () => extractPrevExecutionIds(events),
    [events],
  );
  const artifacts = useMemo(
    () => resolveFileArtifactsForCard(projected.deliveryStatus),
    [projected.deliveryStatus],
  );

  const isMulti = projected.runs.length > 0;
  const team = isMulti
    ? {
        agents: projected.agents,
        runs: projected.runs,
        progress: projected.progress,
        acts: projected.acts,
        teamNotes: projected.teamNotes,
        status: projected.status,
        conversationId: null,
        pendingEscalations,
        escalationsInteractive: false,
        runToolCalls,
        workerToolPhases,
        evidenceLedger: debateEvidenceLedger,
        elapsedMs: turnElapsedMs(events),
        waitProgress,
        detached,
      }
    : undefined;

  const total = current?.events.length ?? 0;

  function selectScenario(name: string) {
    setSearchParams({ s: name });
  }

  const pendingKinds = projected.interactions
    .filter((i) => i.status === "pending")
    .map((i) => i.kind);

  function setFrame(next: number | null) {
    if (!current) return;
    const params: Record<string, string> = { s: current.name };
    if (next !== null && next < total) params.k = String(next);
    setSearchParams(params);
  }

  return (
    <div className="screen">
      <header className="bar">
        <span className="bar-title">前端预览</span>
      </header>

      <div className="preview-controls">
        <label className="preview-label" htmlFor="preview-scenario">
          场景
        </label>
        <select
          id="preview-scenario"
          className="preview-select"
          value={current?.name ?? ""}
          onChange={(e) => selectScenario(e.target.value)}
        >
          {PREVIEW_FIXTURES.map((f) => (
            <option key={f.name} value={f.name}>
              {f.name}
            </option>
          ))}
        </select>
        {current && <p className="muted preview-desc">{current.description}</p>}
        {total > 1 && (
          <div className="preview-scrub">
            <label className="preview-label" htmlFor="preview-frame">
              帧 {frame ?? total}/{total}
            </label>
            <input
              id="preview-frame"
              type="range"
              min={1}
              max={total}
              value={frame ?? total}
              onChange={(e) => {
                const n = Number.parseInt(e.target.value, 10);
                setFrame(n >= total ? null : n);
              }}
            />
          </div>
        )}
      </div>

      <div className="messages preview-messages">
        <div className="bubble user">（预览向量 · 用户消息占位）</div>
        <div className="bubble assistant">
          <AssistantContent
            process={projected.process}
            content={projected.content}
            reasoning={projected.reasoning}
            citations={projected.citations}
            evidenceLedger={projected.evidenceLedger}
            captainContext={projected.captainContext}
            team={team}
            debate={projected.debate}
            debateRounds={projected.debateRounds}
            asks={asks}
            escalationSlots={escalationSlots}
            hotTraces={hotTraces}
            stageCardTraces={stageCardTraces}
            teamPreviewTraces={teamPreviewTraces}
            toolPhases={toolPhases}
            graphAppendActKinds={graphAppendActKinds}
            graphAppendAuthorizedBy={graphAppendAuthorizedBy}
            prevExecutionIds={prevExecutionIds}
          />
          <FileArtifactsCard artifacts={artifacts} conversationId={null} />
          {pendingKinds.length > 0 && (
            <div className="preview-pending muted">
              交互暂停（预览只读）: {pendingKinds.join(", ")}
            </div>
          )}
        </div>
      </div>
      <HangingQuestionBar
        asks={asks.filter((a) => a.status === "pending")}
        readOnly
      />
    </div>
  );
}
