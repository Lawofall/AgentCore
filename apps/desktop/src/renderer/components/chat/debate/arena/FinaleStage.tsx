import { Button } from "@/components/ui";
import type { Execution } from "@/stores/execution";
import { useSidePanelStore } from "@/stores/sidePanel";
import { ModelBadge } from "../ModelBadge";
import {
  type DebateModel,
  formatCrossModelRosterLine,
  stopLabel,
} from "../model";
import { finaleAnchorId } from "./anchors";
import { BriefCard, RoundtableSpectrum } from "./brief";

/** 从各轮发言格取某方实际执行 model（run.model）—— sides 无 wire model 时回退。 */
function sideRunModel(model: DebateModel, sideKey: string): string | undefined {
  for (const round of model.rounds) {
    const side = round.sides.find((s) => s.sideKey === sideKey);
    if (side?.model) return side.model;
  }
  return undefined;
}

/**
 * 决策简报三方署名。仅当 wire 上有任一方 / 裁判 model 时渲染（同模型场零噪声）；
 * sides 缺 model 时回退 run 实际 model；裁判优先 wire moderator_*，再回退主持人 run。
 */
function finaleRosterLine(
  model: DebateModel,
  execution: Execution,
): string | null {
  const sides = model.sides;
  if (!sides?.length) return null;
  const hasWire =
    sides.some((s) => Boolean((s.model ?? "").trim())) ||
    Boolean((model.moderatorModel ?? "").trim());
  if (!hasWire) return null;

  const slots = sides.map((s) => ({
    name: s.name,
    model: (s.model ?? "").trim() || sideRunModel(model, s.key) || null,
    origin: s.origin,
  }));
  const moderatorModel =
    (model.moderatorModel ?? "").trim() ||
    (model.moderatorRunId
      ? (execution.runs.find((r) => r.id === model.moderatorRunId)?.model ?? "")
      : "");
  return formatCrossModelRosterLine(slots, {
    model: moderatorModel || null,
    origin: model.moderatorOrigin,
  });
}

export function FinaleStage({
  model,
  execution,
  messageId,
}: {
  model: DebateModel;
  execution: Execution;
  messageId: string;
}) {
  const showRunDetail = useSidePanelStore((s) => s.showRunDetail);
  const moderatorRun = model.moderatorRunId
    ? execution.runs.find((r) => r.id === model.moderatorRunId)
    : undefined;
  const brief = model.brief;
  const sides = model.sides;
  const hasBrief = !!(brief && sides);
  const rosterLine = finaleRosterLine(model, execution);

  return (
    <div
      id={finaleAnchorId()}
      className="scroll-mt-28 mt-8 border-t-2 border-border pt-6"
    >
      {/* 终审区恒定 max-w-3xl 居中（split 并排下也不随 max-w-7xl 通栏）。 */}
      <div className="mx-auto max-w-3xl">
        <div className="flex flex-wrap items-center gap-2">
          {moderatorRun ? (
            // 对齐 SpeakerBlock 惯例：身份行（标题 + 模型徽章）即钻取入口。
            <Button
              variant="ghost"
              onClick={() =>
                showRunDetail(messageId, moderatorRun.id, "主持人")
              }
              className="h-auto justify-start gap-2 rounded-none px-0 py-0 hover:bg-transparent"
            >
              <h2 className="text-xl font-semibold text-foreground">
                主持人终审
              </h2>
              <ModelBadge model={moderatorRun.model ?? ""} />
            </Button>
          ) : (
            <h2 className="text-xl font-semibold text-foreground">
              主持人终审
            </h2>
          )}
          <span className="text-xs text-muted-foreground">
            {stopLabel(model.stopReason)}
          </span>
        </div>
        {rosterLine && (
          <p
            className="mt-1.5 text-xs text-muted-foreground"
            data-testid="debate-roster-line"
          >
            {rosterLine}
          </p>
        )}

        {hasBrief ? (
          <div className="mt-4 space-y-4">
            {model.form === "roundtable" && (
              <RoundtableSpectrum
                brief={brief}
                sides={sides}
                subtopics={model.subtopics}
              />
            )}
            <BriefCard brief={brief} sides={sides} form={model.form} />
          </div>
        ) : (
          <p className="mt-4 text-sm text-muted-foreground">结论简报生成中…</p>
        )}
      </div>
    </div>
  );
}
