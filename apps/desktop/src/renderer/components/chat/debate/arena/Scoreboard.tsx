import { MANUAL_HELP, ManualHelpLink } from "@/components/ManualHelpLink";
import { ModelBadge } from "../ModelBadge";
import { FINDING_STATUS, findingStatusCounts, gateLabel } from "../findings";
import {
  type DebateModel,
  debateRoster,
  debateSideColorVar,
  modelVendorLabel,
  stopLabel,
} from "../model";
import {
  RISK_LEVELS,
  RISK_SEVERITY,
  buildRiskItems,
  riskCounts,
} from "../severity";
import { HowToReadPopover } from "./HowToReadPopover";
import { closingAnchorId, finaleAnchorId, roundAnchorId } from "./anchors";
import {
  DEBATE_ARENA_PAGE_MAX,
  type DebateArenaLayout,
} from "./debateLayoutPreference";

export function Scoreboard({
  model,
  onScrollTo,
  canSplit,
  layoutMode,
  onLayoutChange,
}: {
  model: DebateModel;
  onScrollTo: (anchorId: string) => void;
  canSplit?: boolean;
  layoutMode?: DebateArenaLayout;
  onLayoutChange?: (mode: DebateArenaLayout) => void;
}) {
  const motion = model.motion ?? model.rounds[0]?.focus ?? "辩论";
  const liveRound = model.rounds.find((r) => r.inFlight);
  const currentRoundNo = liveRound?.roundNo ?? model.rounds.length;
  const totalRounds = model.rounds.length;

  const chapters: { id: string; label: string }[] = model.rounds.map((r) => ({
    id: roundAnchorId(r.roundNo),
    label: `第${r.roundNo}轮`,
  }));
  if (model.settled && model.closings.length > 0) {
    chapters.push({ id: closingAnchorId(), label: "结辩" });
  }
  if (model.settled) {
    chapters.push({ id: finaleAnchorId(), label: "终审" });
  }

  return (
    <div className="border-b border-border">
      <div className={`mx-auto ${DEBATE_ARENA_PAGE_MAX} px-1 py-3`}>
        <div className="flex flex-wrap items-start gap-2">
          <p
            className="min-w-0 flex-1 basis-48 truncate text-base font-medium text-foreground"
            title={motion}
          >
            {motion}
          </p>
          <div className="flex max-w-full shrink-0 flex-wrap items-center justify-end gap-2">
            <StatusLine
              model={model}
              liveRound={liveRound}
              currentRoundNo={currentRoundNo}
              totalRounds={totalRounds}
            />
            <HowToReadPopover form={model.form} />
            <ManualHelpLink to={MANUAL_HELP.debate} />
          </div>
        </div>

        <div className="mt-2">
          <ScoreboardRow2 model={model} />
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2">
          {canSplit && layoutMode && onLayoutChange && (
            <LayoutToggle mode={layoutMode} onChange={onLayoutChange} />
          )}
          <div className="flex flex-1 flex-wrap gap-1">
            {chapters.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => onScrollTo(c.id)}
                className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"
              >
                {c.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusLine({
  model,
  liveRound,
  currentRoundNo,
  totalRounds,
}: {
  model: DebateModel;
  liveRound: DebateModel["rounds"][number] | undefined;
  currentRoundNo: number;
  totalRounds: number;
}) {
  if (model.settled) {
    return (
      <span className="shrink-0 text-xs text-muted-foreground">
        {stopLabel(model.stopReason)}
      </span>
    );
  }

  const speaking = liveRound?.sides.find((s) => s.run?.status === "running");
  const phase = speaking
    ? `${speaking.name}正在${currentRoundNo <= 1 ? "立论" : "续辩"}`
    : liveRound?.crossExam.length
      ? "质询进行中"
      : `第 ${currentRoundNo}/${totalRounds} 轮`;

  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
      <span className="size-1.5 animate-pulse rounded-full bg-primary" />
      {phase}
    </span>
  );
}

function ScoreboardRow2({ model }: { model: DebateModel }) {
  const roster = debateRoster(model.rounds);
  const isVersus = model.form === "debate" && roster.length === 2;

  if (model.form === "roundtable" && model.sides) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        {model.sides.map((s) => (
          <span
            key={s.key}
            className="inline-flex items-center gap-1.5 text-sm text-foreground"
          >
            <span
              className="size-2 rounded-full"
              style={{ backgroundColor: debateSideColorVar(s.key, s.name) }}
            />
            {s.name}
          </span>
        ))}
      </div>
    );
  }

  if (model.form === "red_team" && model.sides) {
    const subject = model.sides.find((s) => s.is_subject);
    const briefFindings = model.brief?.findings ?? [];
    const liveFindings = model.rounds.flatMap((r) => r.findings);
    const findings = briefFindings.length > 0 ? briefFindings : liveFindings;
    const hasFindings = findings.length > 0;
    const risks =
      !hasFindings && model.brief
        ? buildRiskItems(model.sides, model.brief)
        : [];
    const riskTally = riskCounts(risks);
    const statusTally = findingStatusCounts(
      findings.map((f) => ({ status: f.status })),
    );
    const gate = gateLabel(model.brief?.gate);
    return (
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          {subject && (
            <span>
              <span className="text-muted-foreground">方案方 </span>
              <span className="font-medium">{subject.name}</span>
            </span>
          )}
          <span className="text-muted-foreground">·</span>
          <span className="text-muted-foreground">
            红队 {roster.filter((r) => r.sideKey !== subject?.key).length} 人
          </span>
          {gate && (
            <>
              <span className="text-muted-foreground">·</span>
              <span className="font-medium text-foreground">{gate}</span>
            </>
          )}
        </div>
        {hasFindings ? (
          <div className="flex flex-wrap gap-1">
            {(
              [
                "escalated",
                "open",
                "unanswered",
                "answered",
                "deadlocked",
                "closed",
              ] as const
            )
              .filter((s) => (statusTally[s] ?? 0) > 0)
              .map((s) => (
                <span key={s} className={FINDING_STATUS[s].pill}>
                  {FINDING_STATUS[s].label} {statusTally[s]}
                </span>
              ))}
          </div>
        ) : (
          model.settled && (
            <div className="flex gap-1">
              {RISK_LEVELS.filter((l) => riskTally[l] > 0).map((l) => (
                <span key={l} className={RISK_SEVERITY[l].pill}>
                  {RISK_SEVERITY[l].label} {riskTally[l]}
                </span>
              ))}
            </div>
          )
        )}
      </div>
    );
  }

  if (isVersus) {
    const proSide = model.sides?.find((s) => s.stance === "pro");
    const conSide = model.sides?.find((s) => s.stance === "con");
    const proRoster = proSide
      ? roster.find((r) => r.sideKey === proSide.key)
      : roster[0];
    const conRoster = conSide
      ? roster.find((r) => r.sideKey === conSide.key)
      : roster[1];
    if (!proRoster || !conRoster) return null;
    const proModel = sideRunModel(model, proRoster.sideKey);
    const conModel = sideRunModel(model, conRoster.sideKey);
    return (
      <div className="flex flex-wrap items-center justify-between gap-3">
        <VersusSide
          name={proRoster.name}
          model={proModel}
          colorVar={debateSideColorVar(proRoster.sideKey, proRoster.name)}
          align="left"
        />
        <VersusSide
          name={conRoster.name}
          model={conModel}
          colorVar={debateSideColorVar(conRoster.sideKey, conRoster.name)}
          align="right"
        />
      </div>
    );
  }

  if (model.form === "debate" && roster.length > 0) {
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        {roster.map((r) => {
          const runModel = sideRunModel(model, r.sideKey);
          return (
            <span
              key={r.sideKey}
              className="inline-flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-xs text-foreground"
            >
              <span
                className="size-2 shrink-0 rounded-full"
                style={{
                  backgroundColor: debateSideColorVar(r.sideKey, r.name),
                }}
              />
              <span className="font-medium">{r.name}</span>
              {modelVendorLabel(runModel) && (
                <ModelBadge model={runModel ?? ""} />
              )}
            </span>
          );
        })}
      </div>
    );
  }

  return null;
}

/** 从各轮发言格取某方的实际执行 model（run.model），忽略 roster 声称的 per-side model。 */
function sideRunModel(model: DebateModel, sideKey: string): string | undefined {
  for (const round of model.rounds) {
    const side = round.sides.find((s) => s.sideKey === sideKey);
    if (side?.model) return side.model;
  }
  return undefined;
}

function VersusSide({
  name,
  model,
  colorVar,
  align,
}: {
  name: string;
  model?: string;
  colorVar: string;
  align: "left" | "right";
}) {
  const vendor = modelVendorLabel(model);
  return (
    <div
      className={`flex min-w-0 items-center gap-2 text-sm ${align === "right" ? "flex-row-reverse text-right" : ""}`}
    >
      <span
        className="size-2 shrink-0 rounded-full"
        style={{ backgroundColor: colorVar }}
      />
      <span className="font-medium text-foreground">{name}</span>
      {vendor && <ModelBadge model={model ?? ""} />}
    </div>
  );
}

function LayoutToggle({
  mode,
  onChange,
}: {
  mode: DebateArenaLayout;
  onChange: (mode: DebateArenaLayout) => void;
}) {
  return (
    <div className="flex shrink-0 items-center gap-1">
      <span className="text-xs text-muted-foreground">布局</span>
      <div className="flex rounded-lg border border-border p-0.5">
        {(
          [
            { key: "split" as const, label: "并排" },
            { key: "stack" as const, label: "单栏" },
          ] as const
        ).map(({ key, label }) => {
          const active = mode === key;
          return (
            <button
              key={key}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(key)}
              className={`rounded-lg px-2 py-0.5 text-xs font-medium transition-colors ${
                active
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
