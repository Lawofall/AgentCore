import { useLocalTurnBaselineIds } from "@/hooks/useLocalTurnBaselineIds";
import {
  conversationHasRestorableEntry,
  shouldShowComposerChangesHint,
} from "@/lib/conversationFileChanges";
import { useConversationStore } from "@/stores/conversation";
import { runtimeOf } from "@/stores/conversation/runtime";
import { useExecutionStore } from "@/stores/execution";
import { CHANGES_TAB_ID, useSidePanelStore } from "@/stores/sidePanel";
import { Diff } from "lucide-react";
import { useComposerPlusClose, useComposerPlusHost } from "./ComposerPlusMenu";

/**
 * 输入区「改动」提示：本对话有可回滚的文件改动 / 回合基线，且「改动」tab 未挂时出现。
 * 点击显式打开右坞「改动」（不自动占条）。Git chip 仍只管分支，两枚不抢语义。
 */
export function ComposerChangesChip({
  conversationId,
}: {
  conversationId: string | null;
}) {
  const plusHost = useComposerPlusHost();
  const closePlus = useComposerPlusClose();
  const showChanges = useSidePanelStore((s) => s.showChanges);
  const changesOpen = useSidePanelStore((s) => s.changesOpen);
  const isChangesFloating = useSidePanelStore((s) =>
    s.floats.some((f) => f.tabId === CHANGES_TAB_ID),
  );
  const messages = useConversationStore(
    (s) => runtimeOf(s, conversationId).messages,
  );
  const byId = useExecutionStore((s) => s.byId);
  const baselineIds = useLocalTurnBaselineIds(conversationId, messages);
  const hasRestorable = conversationHasRestorableEntry(
    messages,
    byId,
    baselineIds,
  );

  if (plusHost && plusHost.panel !== "list") return null;
  if (
    !shouldShowComposerChangesHint({
      conversationId,
      hasRestorable,
      changesOpen,
      isChangesFloating,
    })
  ) {
    return null;
  }

  return (
    <button
      type="button"
      className="inline-flex h-8 shrink-0 items-center gap-1 rounded-lg px-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
      title="查看这次对话改过的文件"
      aria-label="查看改动"
      data-testid="composer-changes-chip"
      onClick={() => {
        closePlus?.();
        showChanges();
      }}
    >
      <Diff size={12} className="shrink-0" aria-hidden />
      <span>改动</span>
    </button>
  );
}
