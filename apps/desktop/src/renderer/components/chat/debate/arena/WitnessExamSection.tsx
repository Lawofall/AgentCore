import { Markdown } from "@/components/chat/Markdown";
import { Badge, Button } from "@/components/ui";
import { useStreamAwareDisclosure } from "@/stores/disclosure";
import { useSidePanelStore } from "@/stores/sidePanel";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { DebateWitnessExamView } from "../model";
import { ModeratorIdentity } from "./ModeratorIdentity";
import { summarizeText } from "./parseSpeechArguments";

const ANSWER_PREVIEW_LEN = 48;
const QUESTION_PREVIEW_LEN = 72;

/** 证人答问区（批 D1）：主持人点名幕1 透镜证人的事实性问答块。 */
export function WitnessExamSection({
  exchanges,
  messageId,
  sceneKey,
}: {
  exchanges: DebateWitnessExamView[];
  messageId: string;
  sceneKey: string;
}) {
  if (exchanges.length === 0) return null;
  return (
    <div className="space-y-3">
      <div className="mt-3 border-t border-border pt-3 text-center">
        <h4 className="text-base font-semibold text-foreground">证人答问</h4>
        <p className="mt-1 flex flex-wrap items-center justify-center gap-1.5 text-xs text-muted-foreground">
          <ModeratorIdentity gavelSize={13} className="text-xs" />
          <span>点名证人澄清事实</span>
        </p>
      </div>
      {exchanges.map((wx) => (
        <WitnessBlock
          key={wx.witnessKey}
          wx={wx}
          messageId={messageId}
          sceneKey={sceneKey}
        />
      ))}
    </div>
  );
}

function WitnessBlock({
  wx,
  messageId,
  sceneKey,
}: {
  wx: DebateWitnessExamView;
  messageId: string;
  sceneKey: string;
}) {
  const discloseKey = `${sceneKey}:wit:${wx.witnessKey}`;
  const running = wx.answerRun?.status === "running";
  const [open, toggle] = useStreamAwareDisclosure(discloseKey, running, {
    liveDefault: true,
    settledDefault: true,
  });
  const showRunDetail = useSidePanelStore((s) => s.showRunDetail);
  const firstQ = wx.exchanges[0]?.question ?? "";
  const firstA = wx.exchanges[0]?.answer ?? "";

  return (
    <div
      className="rounded-xl border border-border bg-muted/30 px-3 py-2"
      style={{ borderLeftColor: wx.colorVar, borderLeftWidth: 3 }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="inline-flex items-center gap-1 text-sm font-medium text-foreground"
          onClick={toggle}
        >
          {open ? (
            <ChevronDown className="size-3.5" />
          ) : (
            <ChevronRight className="size-3.5" />
          )}
          {wx.name}
        </button>
        {wx.originCaption ? (
          <Badge tone="muted">{wx.originCaption}</Badge>
        ) : null}
        {running ? <Badge tone="primary">作答中</Badge> : null}
        {!open && firstQ ? (
          <span className="truncate text-xs text-muted-foreground">
            {summarizeText(firstQ, QUESTION_PREVIEW_LEN)}
            {firstA ? ` · ${summarizeText(firstA, ANSWER_PREVIEW_LEN)}` : ""}
          </span>
        ) : null}
        {wx.answerRun && messageId ? (
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto h-7 text-xs"
            onClick={() => {
              const runId = wx.answerRun?.id;
              if (!runId) return;
              showRunDetail(messageId, runId, `${wx.name} · 证人答问`);
            }}
          >
            查看完整产出
          </Button>
        ) : null}
      </div>
      {open ? (
        <ol className="mt-2 space-y-2 text-sm">
          {wx.exchanges.map((ex, i) => (
            <li key={`${i}-${ex.question.slice(0, 24)}`} className="space-y-1">
              <p className="text-muted-foreground">
                <span className="font-medium text-foreground">问 · </span>
                {ex.question}
              </p>
              {ex.answer ? (
                <div className="pl-3 text-foreground">
                  <span className="font-medium">答 · </span>
                  <Markdown content={ex.answer} />
                </div>
              ) : (
                <p className="pl-3 text-xs text-muted-foreground">
                  {running ? "作答中…" : "（未作答）"}
                </p>
              )}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
