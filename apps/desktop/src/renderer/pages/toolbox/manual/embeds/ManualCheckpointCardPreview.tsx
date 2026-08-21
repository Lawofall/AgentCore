import { AskUserCard } from "@/components/chat/CheckpointCard";
import type { AskUserContent } from "@/components/chat/ask/AskUserFields";
import {
  ResolvedDecisionRecord,
  askResolvedOutcome,
} from "@/components/chat/decision";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { CheckpointUserDecision } from "@/services/checkpoint";
import { useState } from "react";
import { ManualDemoRetryBar } from "./ManualDemoRetryBar";

const DEMO_ASK: AskUserContent = {
  question:
    "试点范围定多大？\nCEO 建议先小范围验证；两名队员已分别给出成本与回滚方案，需要你拍板后再放行下游任务。",
  assumptions: [],
  questions: [
    {
      id: "scope",
      prompt: "第一批放行范围",
      kind: "choice",
      multiple: false,
      default: "single",
      options: [
        {
          label: "先做一个试点",
          detail: "风险最低，两周可复盘",
          recommended: true,
        },
        {
          label: "同业务线 3 个试点",
          detail: "样本更足，运维压力略高",
        },
        {
          label: "全量放开",
          detail: "最快铺开，需你承担回滚成本",
        },
      ],
    },
  ],
};

type DemoSettled = {
  decision: CheckpointUserDecision;
  note: string;
  selected: string[];
};

function collapsedSummary(settled: DemoSettled): string {
  const note = settled.note.trim();
  if (note) return note;
  if (settled.selected.length > 0) return settled.selected.join(" · ");
  return "";
}

function ManualAskSettledRecord({
  settled,
  disclosureKey,
}: {
  settled: DemoSettled;
  disclosureKey: string;
}) {
  const resolved = askResolvedOutcome("decision", settled.decision);
  return (
    <ResolvedDecisionRecord
      layout="toneStub"
      disclosureKey={disclosureKey}
      tone={resolved.tone}
      icon={resolved.icon}
      label={resolved.label}
      collapsedSummary={collapsedSummary(settled)}
      askIntent="decision"
    >
      <div className="space-y-1.5 pb-3 pl-10 pr-3">
        <p className="whitespace-pre-wrap text-sm text-foreground">
          {DEMO_ASK.question}
        </p>
        {settled.selected.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {settled.selected.map((s) => (
              <span
                key={s}
                className="rounded-full bg-muted px-2 py-0.5 text-xs text-foreground"
              >
                {s}
              </span>
            ))}
          </div>
        )}
        {settled.note ? (
          <p className="whitespace-pre-wrap rounded-lg bg-muted/50 px-2.5 py-1.5 text-xs text-foreground">
            {settled.note}
          </p>
        ) : null}
      </div>
    </ResolvedDecisionRecord>
  );
}

/**
 * 手册「真组件预览」：检查点拍板卡（ask_user 途中味）。
 * 复用 {@link AskUserCard}；提交 / 取消卸卡，换成对话里同一套结算记录，可再试。
 * 自带 TooltipProvider：卡内 ManualHelpLink 依赖它，保证手册页外也可独立渲染。
 */
export function ManualCheckpointCardPreview() {
  const [settled, setSettled] = useState<DemoSettled | null>(null);
  const [cardKey, setCardKey] = useState(0);

  return (
    <TooltipProvider>
      <div
        className="w-full max-w-3xl"
        data-manual-demo-phase={settled ? "settled" : "live"}
      >
        {settled ? (
          <>
            <ManualAskSettledRecord
              settled={settled}
              disclosureKey={`manual-checkpoint-preview:${cardKey}:resolved`}
            />
            <ManualDemoRetryBar
              onRetry={() => {
                setSettled(null);
                setCardKey((k) => k + 1);
              }}
            />
          </>
        ) : (
          <AskUserCard
            key={cardKey}
            content={DEMO_ASK}
            intent="decision"
            onSubmit={(decision, note, selected = []) => {
              setSettled({ decision, note, selected });
            }}
          />
        )}
      </div>
    </TooltipProvider>
  );
}
