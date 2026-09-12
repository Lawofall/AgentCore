import { charCount, outputOf } from "@/components/chat/compare/cells";
import { Button } from "@/components/ui";
import type {
  AgentState,
  ContinuationChain,
  Execution,
} from "@/stores/execution";
import {
  debateBeatFromContext,
  debateBeatLabel,
  isDebateTaggedRun,
} from "@/stores/execution";
import { Columns2 } from "lucide-react";
import { RunStatusDot, Section } from "./shared";

/** 侧面板版本链「对比」深链的预选对：热修编辑链 → 当前版 × 上一版（一眼看这版改了什么），看原始版
 * 时 → 原始 × 最新；辩论链 → undefined（让擂台走自然默认 正 × 反，同侧逐轮 diff 是噪音）。 */
export function continuationComparePair(
  chain: ContinuationChain,
  currentRunId: string,
): [string, string] | undefined {
  const vs = chain.versions;
  const isDebate = vs.some((v) => isDebateTaggedRun(v.run));
  if (isDebate) return undefined;
  const i = vs.findIndex((v) => v.run.id === currentRunId);
  if (i > 0) return [vs[i - 1].run.id, vs[i].run.id];
  return [vs[0].run.id, vs[vs.length - 1].run.id];
}

/**
 * 轮次 / 版本 导航 (辩论逐轮 / 定向唤回「修订 vN」): the run's version chain as a horizontal
 * track — 第 N 轮 (a debate, labelled off the wire `round`) or vN (a 热修), the current version
 * highlighted, every other version a click that jumps the panel to it (上一版↔下一版 without
 * leaving the detail). The panel-scoped twin of the compare view's version track
 * ({@link import("@/components/chat/compare/ContinuationOverview").ContinuationOverview}), reusing the
 * same {@link continuationChains} projection so both read one source. A 热修 edit chip also shows its
 * 改动量 (Δ 字 vs 上一版) so the演进 reads at a glance; the 对比 deep-link opens the full diff.
 */
export function ContinuationChainSection({
  chain,
  currentRunId,
  agents,
  execution,
  onSelect,
  onCompare,
}: {
  chain: ContinuationChain;
  currentRunId: string;
  agents: AgentState[];
  execution: Execution;
  onSelect: (runId: string, role?: string) => void;
  /** 深链画布「对比」透镜看逐版/逐轮改动 (§4.2)；无会话 id 时省略。 */
  onCompare?: () => void;
}) {
  const isDebate = chain.versions.some((v) => isDebateTaggedRun(v.run));
  return (
    <Section
      title={isDebate ? "轮次" : "接续"}
      action={
        onCompare && (
          <Button
            variant="ghost"
            onClick={onCompare}
            className="h-6 shrink-0 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
          >
            <Columns2 size={12} />
            {isDebate ? "对比各轮" : "对比接续"}
          </Button>
        )
      }
    >
      <div className="flex gap-1.5 overflow-x-auto pb-1">
        {chain.versions.map(({ version, run }, idx) => {
          const current = run.id === currentRunId;
          const role =
            agents.find((a) => a.id === run.agentId)?.role ?? run.agentId;
          const label = isDebate
            ? run.continuesRunId == null
              ? `第 ${run.round || version} 轮`
              : debateBeatLabel({
                  round: run.round,
                  continuationIndex: run.continuationIndex,
                  beat: debateBeatFromContext(run.receivedContext),
                })
            : version === 1
              ? "现场"
              : `续 ×${version - 1}`;
          const prevRun = idx > 0 ? chain.versions[idx - 1].run : null;
          const delta =
            !isDebate && prevRun
              ? charCount(outputOf(execution, run)) -
                charCount(outputOf(execution, prevRun))
              : 0;
          return (
            <Button
              key={run.id}
              variant="ghost"
              disabled={current}
              onClick={() => onSelect(run.id, role)}
              className={`h-auto shrink-0 gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs ${
                current
                  ? "border-primary bg-primary/5"
                  : "border-border bg-muted hover:bg-accent"
              }`}
            >
              <span className="flex items-center gap-1.5">
                <RunStatusDot status={run.status} />
                <span className="font-medium text-foreground">{label}</span>
                {delta !== 0 && (
                  <span
                    className={delta > 0 ? "text-success" : "text-destructive"}
                  >
                    {delta > 0 ? `+${delta}` : delta}
                  </span>
                )}
                {current && <span className="text-muted-foreground">当前</span>}
              </span>
            </Button>
          );
        })}
      </div>
    </Section>
  );
}
