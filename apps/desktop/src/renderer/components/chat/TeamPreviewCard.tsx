import { shouldHostPreviewInGraph } from "@/components/chat/debatePreviewPlacement";
import {
  ResolvedDecisionRecord,
  teamCorrectionSuffix,
  teamPreviewLead,
  teamPreviewSettledLead,
  teamResolvedOutcome,
} from "@/components/chat/decision";
import {
  DebatePreviewBody,
  WorkerPreviewRows,
} from "@/components/chat/teamPreview";
import type { TeamPreviewDisplay } from "@/stores/conversation";
import { useMessageExecution } from "@/stores/execution";
import { timelineIntentionalEmpty } from "@/stores/interactions/timelineCardSlot";

/**
 * Inline team_preview card — thin preflight before fan-out / moderator start.
 * Actionable surface is the durable ResumePrompt (挂起即收口). pending 对齐
 * ask_user：{@link timelineIntentionalEmpty}（分工表 / 辩题立场只在拍板卡）。
 * resolved 留可折叠结论文. Bag miss is upstream {@link timelineMissingCard}.
 *
 * Branches on ``primitive``: delegate = 队员分工表; debate = 辩题 / 立场 / 轮次预算.
 *
 * Resolved continue + 编制已在 store：图立刻接管，本卡返回
 * {@link timelineIntentionalEmpty}（see {@link shouldHostPreviewInGraph}）—
 * 图已出现则不画废卡.
 * 同泡仍有 pending 开工卡时 leftover go 不藏（与出图同一套闸）.
 * 取消 / 调整回灌 / 超时 / 尚未铺节点时仍留一行结论文.
 *
 * Resolved copy / icons come from the shared decision meta ({@link TEAM_PRIMITIVE_META}).
 */
export function TeamPreviewCard({
  preview,
  messageId,
  bubblePreviews,
}: {
  preview: TeamPreviewDisplay;
  /** Assistant message id — used to hide the spare timeline card once the graph takes over. */
  messageId?: string;
  /** Same-bubble team_preview cards — pending sibling blocks leftover-go hide. */
  bubblePreviews?: readonly Pick<TeamPreviewDisplay, "status" | "decision">[];
}) {
  const execution = useMessageExecution(messageId ?? null);
  if (shouldHostPreviewInGraph(preview, execution?.runs, bubblePreviews)) {
    return timelineIntentionalEmpty();
  }
  if (preview.status === "resolved") {
    return <ResolvedTeamPreview preview={preview} />;
  }
  return timelineIntentionalEmpty();
}

function isDebate(preview: TeamPreviewDisplay): boolean {
  return preview.primitive === "debate";
}

function summarySuffix(preview: TeamPreviewDisplay): string {
  const args = {
    primitive: preview.primitive,
    headline: preview.headline,
    workerCount: preview.workers.length,
    sideCount: preview.sides.length,
  };
  if (preview.status === "resolved" && preview.decision === "continue") {
    return teamPreviewSettledLead(args);
  }
  return teamPreviewLead(args);
}

function ResolvedTeamPreview({ preview }: { preview: TeamPreviewDisplay }) {
  const decision = preview.decision ?? "timeout";
  const meta = teamResolvedOutcome(
    preview.primitive,
    decision,
    Boolean(preview.note?.trim()),
  );
  const correction = teamCorrectionSuffix({
    excluded_run_ids: preview.excluded_run_ids,
    write_capability_overrides: preview.write_capability_overrides,
  });
  const suffix = summarySuffix(preview);
  const summary = suffix
    ? `${meta.label}${correction} · ${suffix}`
    : `${meta.label}${correction}`;

  return (
    <ResolvedDecisionRecord
      layout="neutralCollapsible"
      disclosureKey={`team-preview:${preview.id}`}
      icon={meta.icon}
      summary={summary}
    >
      {isDebate(preview) ? (
        <DebatePreviewBody debate={preview} />
      ) : (
        <WorkerPreviewRows workers={preview.workers} />
      )}
      {preview.note && (
        <p className="mt-1.5 whitespace-pre-wrap rounded-lg bg-muted/50 px-2.5 py-1.5 text-xs text-foreground">
          {preview.note}
        </p>
      )}
    </ResolvedDecisionRecord>
  );
}
