import { Scoreboard } from "@/components/chat/debate/arena/Scoreboard";
import { TooltipProvider } from "@/components/ui/tooltip";
import { DEMO_DEBATE_EXECUTION, DEMO_DEBATE_MODEL } from "./demoDebate";

/**
 * 手册「真组件预览」：辩论室记分牌。
 * 复用 {@link Scoreboard} + 手造已收场 DebateModel。
 * 自带 TooltipProvider（手册入口 tooltip）；手册深链需外层 Router（产品手册页已有）。
 */
export function ManualDebateScoreboardPreview() {
  return (
    <TooltipProvider>
      <div className="w-full max-w-3xl overflow-hidden rounded-xl border border-border bg-card">
        <Scoreboard
          model={DEMO_DEBATE_MODEL}
          execution={DEMO_DEBATE_EXECUTION}
          onScrollTo={() => {}}
        />
      </div>
    </TooltipProvider>
  );
}
