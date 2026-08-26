import {
  collectSuccessfulFileWrites,
  formatWholeFilePasteHint,
  shouldShowWholeFilePasteHint,
} from "@/lib/wholeFilePasteHint";
import type { ExecutionJournal } from "@/stores/execution/types";
import type { ProcessStep } from "@/types/events";
import { AlertTriangle } from "lucide-react";

/**
 * B′paste：无写盘成功且正文像「请用户整文件替换交差」时的可忽略提示。
 * 不改正文、不恢复【落盘说明】横幅。
 */
export function WholeFilePasteHint({
  content,
  process,
  journal,
}: {
  content: string | undefined;
  process: ProcessStep[] | undefined;
  journal?: ExecutionJournal | null;
}) {
  const writes = collectSuccessfulFileWrites(process, journal);
  if (
    !shouldShowWholeFilePasteHint({
      content,
      hasSuccessfulWrites: writes.length > 0,
    })
  ) {
    return null;
  }

  return (
    <output
      className="mt-2 flex items-start gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
      data-testid="whole-file-paste-hint"
    >
      <AlertTriangle size={14} className="mt-0.5 shrink-0" />
      <span>{formatWholeFilePasteHint()}</span>
    </output>
  );
}
