import { Button } from "@/components/ui";
import { useConversations } from "@/hooks/useConversations";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { STARTER_TASK_CHIPS, resolveDraftEmptyKind } from "@/lib/onboarding";
import { useComposerDraftStore } from "@/stores/composer";
import { BookOpen } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

/**
 * 草稿页空态两态：首启任务 chips / 老用户单句。平台代付、开箱即用——无「先接入模型」门。
 * 仅在 ChatView 无消息时渲染。
 */
export function DraftEmptyState({
  previewKind,
}: {
  /** Offline preview override. */
  previewKind?: ReturnType<typeof resolveDraftEmptyKind>;
}) {
  const conversations = useConversations();
  const kind = previewKind ?? resolveDraftEmptyKind({ conversations });
  const fill = useComposerDraftStore((s) => s.fill);
  const { isNarrow } = useNarrowLayoutState();
  const [howToOpen, setHowToOpen] = useState(false);

  if (kind === "starter_chips") {
    return (
      <div
        className="mx-auto max-w-lg px-6 text-center"
        data-empty-kind="starter_chips"
      >
        <p className="text-2xl font-medium text-foreground">
          今天想解决什么问题？
        </p>
        <p className="mt-2 text-xs text-muted-foreground">
          试试这些会拉起多 Agent 协作的任务——点一下填入输入框，再按发送。
        </p>
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {STARTER_TASK_CHIPS.map((text) => (
            <Button
              key={text}
              variant="neutral"
              className="h-auto max-w-full whitespace-normal border border-border bg-card px-3 py-2 text-left text-muted-foreground hover:border-primary/40 hover:text-foreground"
              onClick={() => fill(text)}
            >
              {text}
            </Button>
          ))}
        </div>
        <button
          type="button"
          className="mt-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setHowToOpen((open) => !open)}
          aria-expanded={howToOpen}
        >
          <BookOpen size={12} />
          怎么用
        </button>
        {howToOpen && <HowToPanel showManual={!isNarrow} />}
      </div>
    );
  }

  return (
    <div className="text-center" data-empty-kind="returning">
      <p className="text-2xl font-medium text-foreground">
        今天想解决什么问题？
      </p>
    </div>
  );
}

function HowToPanel({ showManual }: { showManual: boolean }) {
  return (
    <div className="mx-auto mt-3 max-w-sm text-left text-sm text-muted-foreground">
      <p>
        你一句话说要做什么就行。简单的直接回答；比较大的事会自己找人一起做。做到一半需要你决定时，会停下来问你。做完的东西会放到文件里。
      </p>
      {showManual && (
        <Link
          to="/toolbox/manual"
          className="mt-2 inline-block text-xs hover:text-foreground"
        >
          产品手册
        </Link>
      )}
    </div>
  );
}
