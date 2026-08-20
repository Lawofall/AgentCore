import { AskUserCard } from "@/components/chat/CheckpointCard";
import type { AskUserContent } from "@/components/chat/ask/AskUserFields";
import { TooltipProvider } from "@/components/ui/tooltip";

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

/**
 * 手册「真组件预览」：检查点拍板卡（ask_user 途中味）。
 * 复用 {@link AskUserCard}（ResumePrompt / 时间线检查点的同一交互体）；提交为空操作。
 * 自带 TooltipProvider：卡内 ManualHelpLink 依赖它，保证手册页外也可独立渲染。
 */
export function ManualCheckpointCardPreview() {
  return (
    <TooltipProvider>
      <div className="w-full max-w-3xl">
        <AskUserCard
          content={DEMO_ASK}
          intent="decision"
          onSubmit={async () => {}}
        />
      </div>
    </TooltipProvider>
  );
}
