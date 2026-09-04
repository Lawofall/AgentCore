import { Loader2 } from "lucide-react";
import {
  type DebateForm,
  type DebateRoundModel,
  describeRoundVerdict,
} from "../model";
import { ModeratorIdentity } from "./ModeratorIdentity";

/** 裁判札记横带：逐轮小结 / 小结空窗 / 拟质询空窗。身份壳与开场入场、质询报幕一致。 */
export function JudgeNote({
  text,
  round,
  form,
  pending,
  pendingKind = "summary",
}: {
  text: string;
  round?: DebateRoundModel;
  form?: DebateForm;
  pending?: boolean;
  /** pending 文案分流：拟质询空窗 vs 小结空窗。缺省小结（向后兼容）。 */
  pendingKind?: "cross_exam" | "summary";
}) {
  if (pending) {
    return (
      <div className="flex items-center gap-2 border-y border-border bg-muted/30 px-3 py-2.5 text-xs text-muted-foreground">
        <ModeratorIdentity gavelSize={13} className="text-xs" />
        <Loader2 size={13} className="animate-spin shrink-0" />
        <span>
          {pendingKind === "cross_exam" ? "主持人正在拟质询…" : "正在小结…"}
        </span>
      </div>
    );
  }

  const verdict = round?.verdict;
  const status = verdict && form ? describeRoundVerdict(verdict, form) : null;

  return (
    <div className="border-y border-border bg-muted/20 px-3 py-2.5">
      <div className="mb-1">
        <ModeratorIdentity gavelSize={14} className="text-xs" />
      </div>
      <div className="min-w-0">
        <p className="text-sm text-foreground">{text}</p>
        {status && (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
            <span className="text-muted-foreground">{status.label}</span>
          </div>
        )}
      </div>
    </div>
  );
}
