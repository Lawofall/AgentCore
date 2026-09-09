import { RunDetailBody } from "@/components/chat/detail/RunDetailBody";
import { IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useStickToBottom } from "@/lib/useStickToBottom";
import { useMessageRun } from "@/stores/execution";
import { ArrowDown } from "lucide-react";
import { type KeyboardEvent, useLayoutEffect, useState } from "react";

/**
 * Scroll shell for a SidePanel run tab: stick-to-bottom while the worker is
 * live (same semantics as the main chat / IM thread), open finished runs at the
 * top. Lives outside {@link RunDetailBody} so the panel chrome does not subscribe
 * to every streaming token — only this shell + the body do.
 *
 * Docked inactive tabs unmount this shell. Remount follows {@link useStickToBottom}
 * reset: live → bottom, settled → top. Visible float hosts keep their own copy.
 */
export function RunDetailScroll({
  messageId,
  runId,
}: {
  messageId: string;
  runId: string;
}) {
  const viewed = useMessageRun(messageId, runId);
  const ready = viewed != null;
  const live =
    ready &&
    (viewed.agent.status === "working" || viewed.run.status === "running");

  // Only fire reset once the run is projectable — avoids a false "done → top"
  // flash before execution lands, then a second reset when data arrives.
  const resetKey = ready ? `${messageId}:${runId}` : null;

  const { scrollRef, contentRef, atBottom, jumpToBottom } = useStickToBottom(
    resetKey,
    { followOnReset: live },
  );
  const [scrollEl, setScrollEl] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    setScrollEl(scrollRef.current);
  }, [scrollRef]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "End") return;
    e.preventDefault();
    jumpToBottom();
  };

  return (
    <div className="absolute inset-0">
      <div
        ref={scrollRef}
        className="h-full overflow-y-auto"
        // biome-ignore lint/a11y/useSemanticElements: scroll pane needs HTMLDivElement for overflow + hook ref typing; region role + label carry the semantics.
        role="region"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: scroll region must be focusable for End→bottom and keyboard scrolling; no native scroll-pane element.
        tabIndex={0}
        aria-label="运行详情"
        onKeyDown={onKeyDown}
      >
        <div ref={contentRef}>
          {scrollEl ? (
            <RunDetailBody
              key={`${messageId}:${runId}`}
              messageId={messageId}
              runId={runId}
              scrollParent={scrollEl}
            />
          ) : null}
        </div>
      </div>
      {!atBottom && (
        <SimpleTooltip label="回到底部">
          <IconButton
            size="md"
            onClick={jumpToBottom}
            aria-label="回到底部"
            className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border border-border bg-card text-muted-foreground shadow-md hover:text-foreground"
          >
            <ArrowDown size={16} />
          </IconButton>
        </SimpleTooltip>
      )}
    </div>
  );
}
