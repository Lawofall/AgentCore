import { ModeratorIdentity } from "./ModeratorIdentity";

/**
 * 主持人入场：开篇身份壳 + 定场引言。
 * 轻于终审舞台、与每轮小结横带平级或略轻；左竖线题记，不盖过第 1 轮标题。
 */
export function OpeningNote({ text }: { text: string }) {
  return (
    <div className="border-l-2 border-border py-0.5 pl-3">
      <ModeratorIdentity gavelSize={13} className="text-xs" />
      <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
        {text}
      </p>
    </div>
  );
}
