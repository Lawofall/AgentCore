import type { Execution } from "@/stores/execution";
import { useCallback, useState } from "react";
import { type DebateClashView, type DebateModel, isFlatRound } from "../model";
import { CrossExamSection } from "./CrossExamSection";
import { FindingThreads } from "./FindingThreads";
import { JudgeNote } from "./JudgeNote";
import { resolveModeratorModel } from "./ModeratorIdentity";
import { OpeningNote } from "./OpeningNote";
import { SectionHeader } from "./SectionHeader";
import { SpeakerBlock, speechStageLabel } from "./SpeakerBlock";
import { ThreadTurns } from "./ThreadTurns";
import { UserInterjection } from "./UserInterjection";
import { WitnessExamSection } from "./WitnessExamSection";
import { roundAnchorId, speakerAnchorId } from "./anchors";
import {
  DEBATE_SPLIT_GRID,
  type DebateArenaLayout,
  partitionSides,
} from "./debateLayoutPreference";
import { openingText } from "./openingText";

export function Transcript({
  model,
  execution,
  messageId,
  layoutMode = "stack",
}: {
  model: DebateModel;
  execution: Execution;
  messageId: string;
  layoutMode?: DebateArenaLayout;
}) {
  const topicMotion = model.motion ?? model.rounds[0]?.focus ?? "";
  const openingLine = openingText(model);
  const moderatorModel = resolveModeratorModel(model, execution);

  const lastRoundBySideKey = new Map<string, number>();
  for (const r of model.rounds) {
    for (const s of r.sides) {
      if (s.sideKey) lastRoundBySideKey.set(s.sideKey, r.roundNo);
    }
  }

  const [highlightId, setHighlightId] = useState<string | null>(null);

  const scrollToSpeaker = useCallback(
    (clash: DebateClashView) => {
      const prevRound = lastRoundBySideKey.get(clash.toKey);
      if (!prevRound) return;
      const id = speakerAnchorId(prevRound, clash.toKey);
      setHighlightId(id);
      document.getElementById(id)?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    },
    [lastRoundBySideKey],
  );

  const renderSpeakerBlock = (
    side: (typeof model.rounds)[number]["sides"][number],
    round: (typeof model.rounds)[number],
  ) => {
    const replies =
      round.roundNo >= 2
        ? round.clashes.filter((c) => c.fromKey === side.sideKey)
        : [];
    return (
      <SpeakerBlock
        key={side.key}
        side={side}
        round={round}
        execution={execution}
        messageId={messageId}
        stage={speechStageLabel(round.roundNo)}
        highlight={
          highlightId ===
          speakerAnchorId(round.roundNo, side.sideKey || side.key)
        }
        onHighlightEnd={() => setHighlightId(null)}
        clashes={replies}
        onClashClick={scrollToSpeaker}
      />
    );
  };

  const useSplit = layoutMode === "split" && model.form === "debate";
  // 红队不渲染质询/结辩区块（三拍取代质询；结辩已移除）。
  const showCrossExam = model.form === "debate";

  return (
    <div className="space-y-1">
      {openingLine && <OpeningNote text={openingLine} model={moderatorModel} />}

      {model.rounds.map((round) => {
        const flat = isFlatRound(round);
        const hasFindings = round.findings.length > 0;
        const hasThread = round.threadTurns.length > 0;
        // 红队有 finding → 线程英雄区；圆桌有 thread_turns → 线程英雄区；否则旧单波按方分栏。
        const useFindingHero = model.form === "red_team" && hasFindings;
        const useThreadHero = model.form === "roundtable" && hasThread;
        const speechSides = useFindingHero
          ? []
          : useThreadHero
            ? []
            : uniqueSpeechSides(round.sides);

        const allDone =
          round.sides.length > 0 &&
          round.sides.every((s) => s.run && s.run.status !== "running");
        const findingRunning = round.findings.some(
          (f) =>
            f.attackRun?.status === "running" ||
            f.responseRun?.status === "running" ||
            f.rebuttalRun?.status === "running",
        );
        const threadRunning = round.threadTurns.some(
          (t) => t.run?.status === "running",
        );
        const showModeratorPending =
          round.inFlight && allDone && !findingRunning && !threadRunning;
        const crossExamRunning =
          showCrossExam &&
          round.crossExam.some((cx) => cx.answerRun?.status === "running");
        const pendingKind =
          showCrossExam &&
          model.crossExamEnabled &&
          round.crossExam.length === 0 &&
          !crossExamRunning
            ? "cross_exam"
            : "summary";
        const focusText =
          round.focus && round.focus !== topicMotion ? round.focus : "";

        return (
          <div key={round.roundNo}>
            {!flat && round.roundNo >= 1 && (
              <SectionHeader
                id={roundAnchorId(round.roundNo)}
                label={`第 ${round.roundNo} 轮`}
                sublabel={focusText || undefined}
              />
            )}

            {round.userInterjections.map((it, i) => (
              <UserInterjection
                key={`${it.ask}-${i}`}
                interjection={it}
                sides={round.sides}
              />
            ))}

            {useFindingHero ? (
              <FindingThreads
                findings={round.findings}
                execution={execution}
                messageId={messageId}
              />
            ) : useThreadHero ? (
              <ThreadTurns
                turns={round.threadTurns}
                execution={execution}
                messageId={messageId}
                subtopic={focusText || round.focus}
              />
            ) : useSplit ? (
              <div className={DEBATE_SPLIT_GRID}>
                {(() => {
                  const { pro, con } = partitionSides(
                    speechSides,
                    (s) => s.sideKey,
                    (s) => s.stance,
                  );
                  return (
                    <>
                      <div className="min-w-0">
                        {pro && renderSpeakerBlock(pro, round)}
                      </div>
                      <div className="min-w-0">
                        {con && renderSpeakerBlock(con, round)}
                      </div>
                    </>
                  );
                })()}
              </div>
            ) : (
              speechSides.map((side) => renderSpeakerBlock(side, round))
            )}

            {showCrossExam && round.crossExam.length > 0 && (
              <CrossExamSection
                exchanges={round.crossExam}
                messageId={messageId}
                sceneKey={`${messageId}:cx:r${round.roundNo}`}
                layoutMode={layoutMode}
                moderatorModel={moderatorModel}
              />
            )}

            {round.witnessExam.length > 0 && (
              <WitnessExamSection
                exchanges={round.witnessExam}
                messageId={messageId}
                sceneKey={`${messageId}:wit:r${round.roundNo}`}
                moderatorModel={moderatorModel}
              />
            )}

            {round.summary && !round.inFlight ? (
              <JudgeNote
                text={round.summary}
                round={round}
                form={model.form}
                model={moderatorModel}
              />
            ) : (
              showModeratorPending &&
              !crossExamRunning && (
                <JudgeNote
                  text=""
                  pending
                  pendingKind={pendingKind}
                  model={moderatorModel}
                />
              )
            )}
          </div>
        );
      })}
    </div>
  );
}

/**
 * 同方多 beat（红队攻/复攻）时发言格去重：每 sideKey 只留首次（通常是攻击波 / 线程开题）。
 * finding / thread 英雄区不用此列表。
 */
function uniqueSpeechSides(
  sides: DebateModel["rounds"][number]["sides"],
): DebateModel["rounds"][number]["sides"] {
  const seen = new Set<string>();
  const out: typeof sides = [];
  for (const s of sides) {
    const k = s.sideKey || s.key;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(s);
  }
  return out;
}
