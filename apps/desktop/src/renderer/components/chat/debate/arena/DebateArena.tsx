import { EvidenceLedgerProvider } from "@/components/chat/EvidenceLedgerContext";
import { buildLedgerMap } from "@/lib/evidenceLedger";
import type { Execution } from "@/stores/execution";
import { useCallback, useMemo, useRef, useState } from "react";
import { toDebateModel } from "../model";
import { ClosingBlocks } from "./ClosingBlocks";
import { FinaleStage } from "./FinaleStage";
import { Scoreboard } from "./Scoreboard";
import { Transcript } from "./Transcript";
import {
  DEBATE_ARENA_CONTAINER,
  DEBATE_ARENA_PAGE_MAX,
  type DebateArenaLayout,
  canUseSplitLayout,
  loadDebateArenaLayout,
  saveDebateArenaLayout,
} from "./debateLayoutPreference";

/**
 * 辩论室赛事页：记分牌 + 阶段化剧本主列 + 终审舞台（记分牌随内容滚动，不占 sticky 屏）。
 */
export function DebateArena({
  execution,
  messageId,
}: {
  execution: Execution;
  messageId: string;
  conversationId: string | null;
  interactive: boolean;
}) {
  const model = toDebateModel(execution);
  const scrollRef = useRef<HTMLDivElement>(null);
  const ledgerMap = useMemo(
    () => (model ? buildLedgerMap(model.evidenceLedger) : null),
    [model],
  );

  const scrollToAnchor = useCallback((anchorId: string) => {
    const root = scrollRef.current;
    const el = root?.querySelector(`#${CSS.escape(anchorId)}`);
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  const [layoutMode, setLayoutMode] = useState<DebateArenaLayout>(() =>
    loadDebateArenaLayout(),
  );

  const handleLayoutChange = useCallback((mode: DebateArenaLayout) => {
    setLayoutMode(mode);
    saveDebateArenaLayout(mode);
  }, []);

  if (!model) return null;

  const canSplit = canUseSplitLayout(model);
  const effectiveLayout = canSplit ? layoutMode : "stack";

  return (
    <EvidenceLedgerProvider ledger={ledgerMap}>
      <div
        ref={scrollRef}
        className={`mx-auto w-full ${DEBATE_ARENA_CONTAINER} ${DEBATE_ARENA_PAGE_MAX}`}
      >
        <Scoreboard
          model={model}
          execution={execution}
          onScrollTo={scrollToAnchor}
          canSplit={canSplit}
          layoutMode={effectiveLayout}
          onLayoutChange={handleLayoutChange}
        />
        <div
          className={`min-w-0 px-1 py-4 ${effectiveLayout === "split" ? "w-full" : "mx-auto max-w-3xl"}`}
        >
          <Transcript
            model={model}
            execution={execution}
            messageId={messageId}
            layoutMode={effectiveLayout}
          />
          {model.settled && model.closings.length > 0 && (
            <ClosingBlocks
              closings={model.closings}
              execution={execution}
              messageId={messageId}
              layoutMode={effectiveLayout}
            />
          )}
          {model.settled && (
            <FinaleStage
              model={model}
              execution={execution}
              messageId={messageId}
            />
          )}
        </div>
      </div>
    </EvidenceLedgerProvider>
  );
}
