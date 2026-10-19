import { useWorkspaceModeState } from "@/components/workspace/WorkspaceModeControl";
import { useGitRepoStatus } from "@/hooks/useGitRepoStatus";
import { useConversationWorkspace } from "@/hooks/useWorkspaces";
import { hasLocalFiles } from "@/lib/capabilities";
import { useSidePanelStore } from "@/stores/sidePanel";
import { GitBranch } from "lucide-react";
import { useComposerPlusClose, useComposerPlusHost } from "./ComposerPlusMenu";

/**
 * U1 会话条只读 Git chip：分支名 + dirty 点 + ahead/behind。
 * 点击打开「改动」。仅当前文件夹根有 `.git` 时出现（干净仓也显示分支）。
 * 「改动」下面的 Git 列表另有货才出。
 */
export function ComposerGitStatusChip({
  conversationId,
}: {
  conversationId: string | null;
}) {
  const state = useWorkspaceModeState(conversationId);
  const convWs = useConversationWorkspace(conversationId);
  const showChanges = useSidePanelStore((s) => s.showChanges);
  const plusHost = useComposerPlusHost();
  const closePlus = useComposerPlusClose();
  const canProbe =
    hasLocalFiles() &&
    !!convWs &&
    !!state?.effective.isLocal &&
    !!state.effective.rootId &&
    !state.effective.rootMissing &&
    !state.effective.rootStale;

  const { status } = useGitRepoStatus(
    canProbe ? state?.effective.rootId : null,
    canProbe,
    convWs?.subpath ?? "",
  );

  if (plusHost && plusHost.panel !== "list") return null;
  if (!status) return null;

  const syncBits: string[] = [];
  if (status.ahead > 0) syncBits.push(`↑${status.ahead}`);
  if (status.behind > 0) syncBits.push(`↓${status.behind}`);
  const syncLabel = syncBits.join(" ");

  const titleParts = [status.branch];
  if (status.dirty) titleParts.push("这个文件夹有未提交改动");
  if (syncLabel) titleParts.push(syncLabel);
  titleParts.push("打开改动");
  const title = titleParts.join(" · ");

  return (
    <button
      type="button"
      className={
        plusHost
          ? "inline-flex h-8 w-full max-w-none items-center justify-start gap-1 rounded-lg px-2 text-sm text-muted-foreground hover:bg-accent/60 hover:text-foreground"
          : "inline-flex h-8 max-w-[168px] shrink items-center gap-1 rounded-lg px-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
      }
      title={title}
      aria-label={title}
      data-testid="composer-git-status-chip"
      onClick={() => {
        closePlus?.();
        showChanges();
      }}
    >
      <GitBranch size={12} className="shrink-0" aria-hidden />
      <span className="min-w-0 truncate">{status.branch}</span>
      {syncLabel ? (
        <span className="shrink-0 tabular-nums text-muted-foreground/80">
          {syncLabel}
        </span>
      ) : null}
      {status.dirty ? (
        <span
          className="inline-block size-1.5 shrink-0 rounded-full bg-warning"
          aria-label="有未提交改动"
        />
      ) : null}
    </button>
  );
}
