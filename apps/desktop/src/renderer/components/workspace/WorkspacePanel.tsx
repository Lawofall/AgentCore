import { EmptyHint, IconButton } from "@/components/files/parts";
import { Button } from "@/components/ui";
import { useConversationFileSource } from "@/hooks/useConversationFileSource";
import { getConversations } from "@/hooks/useConversations";
import { useExternalGrants } from "@/hooks/useExternalGrants";
import { useConversationWorkspace } from "@/hooks/useWorkspaces";
import { hasLocalFiles } from "@/lib/capabilities";
import { createLocalRootSource } from "@/services/sources/localRootSource";
import { useConversationStore } from "@/stores/conversation";
import { useFoldersStore } from "@/stores/folders";
import { FolderOpen, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { ExternalMountsSection } from "./ExternalMountsSection";
import { FilesSection } from "./FilesSection";
import { LocalTrashSection, TrashSection } from "./TrashSection";
import { WorkspaceClientTools } from "./WorkspaceClientTools";
import { WorkspaceModeBar } from "./WorkspaceModeBar";

/**
 * Workspace mode of the conversation side panel — the file-in/out surface for a
 * conversation's project space (双模式工作区). Files are the panel's always-on body;
 * this view injects workspace-level affordances into the files toolbar's single
 * header row (FileBrowser owns that row): 云端/本地选择器 (leading) plus 软删区
 * (trailing). The shell (SidePanel) owns the frame / resize / close.
 *
 * 快照不在此面板。命名版本在文件页根右键「版本…」（云端「我的文件」/ 本机「本机文件夹」）；
 * 右坞「改动」tab 只审本对话 AI 改动与回合基线回滚（不含留版本）。
 *
 * 交接（把活交给云端团队）已下沉为对话时间线里的「后台云端任务」卡（交接「方案 B」/
 * `BackgroundTaskCard`），完成后就地内联评审应用，不再占用工作区侧栏的独立入口。
 * 协作桌就是当前对话绑定的那棵 `folder:` 树（右坞不再叠第二根）。
 *
 * A draft conversation (no id yet) has no server workspace, so it shows an empty
 * hint until the first turn persists it. 规矩在工具箱的账号库，不挂在这棵文件树上。
 */
export function WorkspaceMode() {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const draftKind = useFoldersStore((s) => s.draftWorkspaceIntent.kind);
  const [trashOpen, setTrashOpen] = useState(false);

  // 与文件中枢同一份数据 + 同一个解析器：对话→其工作区(WorkspaceInfo)→FileSource。本地走桌面
  // IPC、云端走 REST，故 Agent 在本地写的文件这里也能列出（修复「写在本地、读在云端」）。
  const ws = useConversationWorkspace(conversationId);
  const fsAvailable = hasLocalFiles();
  const convSource = useConversationFileSource(conversationId);
  const grants = useExternalGrants(conversationId);
  const working = (grants.data ?? []).find((g) => g.working_root);
  const source =
    working && fsAvailable
      ? createLocalRootSource(working.root_id, working.label)
      : convSource;

  useEffect(() => {
    const sourceKind: "local" | "cloud" | null =
      source === null
        ? null
        : source.id.startsWith("local:")
          ? "local"
          : "cloud";
    console.warn(
      `[FilePreview] workspace source selection ${JSON.stringify({
        wsExists: !!ws,
        ...(ws ? { location: ws.location, rootId: ws.rootId } : {}),
        fsAvailable,
        sourceKind,
        sourceId: source?.id ?? null,
      })}`,
    );
  }, [ws, fsAvailable, source]);

  if (!conversationId) {
    const localDraft = draftKind === "quick_local";
    return (
      <EmptyHint
        inline
        icon={<FolderOpen size={26} className="text-muted-foreground/40" />}
        title={localDraft ? "本地对话" : "云端对话"}
      />
    );
  }

  const emptyTreeHint = working
    ? "这个文件夹里还没有列出文件。"
    : "工作区暂无文件。快速对话可在输入区添加文件夹。";

  const isCloudWorkspace = ws?.location === "cloud";
  const localRootId = ws?.location === "local" ? ws.rootId : null;
  const onCloneGit = isCloudWorkspace
    ? () => {
        const conv = getConversations().find((c) => c.id === conversationId);
        const wsId = conv?.folderId
          ? `folder:${conv.folderId}`
          : `conv:${conversationId}`;
        useFoldersStore.getState().openConnectGit(wsId);
      }
    : undefined;

  return (
    <div className="relative flex h-full flex-col">
      {/* 单行面板头：云端选择器（leading）+ 文件操作 + 软删区（trailing）合到
          FilesSection 的工具栏一行（文件操作经其内部 FileTree 的 ref 驱动），不再单独占一行。 */}
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1">
          <FilesSection
            source={source}
            emptyTreeHint={emptyTreeHint}
            onCloneGit={onCloneGit}
            leading={<WorkspaceModeBar conversationId={conversationId} />}
            trailing={
              <>
                <WorkspaceClientTools source={source} />
                {source?.caps.snapshots ? (
                  <IconButton title="软删区" onClick={() => setTrashOpen(true)}>
                    <Trash2 size={14} />
                  </IconButton>
                ) : localRootId ? (
                  <IconButton title="软删区" onClick={() => setTrashOpen(true)}>
                    <Trash2 size={14} />
                  </IconButton>
                ) : null}
              </>
            }
          />
        </div>
      </div>

      <ExternalMountsSection conversationId={conversationId} />

      {trashOpen && (
        <div className="absolute inset-0 z-20 flex">
          <Button
            variant="ghost"
            aria-label="关闭软删区"
            onClick={() => setTrashOpen(false)}
            className="min-w-0 flex-1 rounded-none bg-overlay/40 p-0"
          />
          <div className="flex w-[85%] max-w-[420px] flex-col border-l border-border bg-card shadow-lg animate-dropdown-in">
            <div className="flex h-9 shrink-0 items-center gap-1.5 border-b border-border pl-3 pr-1">
              <Trash2 size={13} className="shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate text-xs font-medium">
                软删区
              </span>
              <IconButton title="关闭" onClick={() => setTrashOpen(false)}>
                <X size={14} />
              </IconButton>
            </div>
            <div className="min-h-0 flex-1">
              {isCloudWorkspace ? (
                <TrashSection conversationId={conversationId} />
              ) : localRootId ? (
                <LocalTrashSection rootId={localRootId} />
              ) : null}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
