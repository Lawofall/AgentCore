import { EmptyHint, IconButton } from "@/components/files/parts";
import { Button } from "@/components/ui";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { useConversationFileSource } from "@/hooks/useConversationFileSource";
import { getConversations } from "@/hooks/useConversations";
import { useConversationWorkspace } from "@/hooks/useWorkspaces";
import { hasLocalFiles } from "@/lib/capabilities";
import {
  exportCloudDeskToPickedFolder,
  exportCloudDeskZip,
} from "@/services/cloudDeskExit";
import { useConversationStore } from "@/stores/conversation";
import { useFoldersStore } from "@/stores/folders";
import {
  Download,
  FolderDown,
  FolderOpen,
  Loader2,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { ExternalMountsSection } from "./ExternalMountsSection";
import { FilesSection } from "./FilesSection";
import { SharedMountsSection } from "./SharedMountsSection";
import { LocalTrashSection, TrashSection } from "./TrashSection";
import { WorkspaceClientTools } from "./WorkspaceClientTools";
import { WorkspaceModeBar } from "./WorkspaceModeBar";

/**
 * Workspace mode of the conversation side panel — the file-in/out surface for a
 * conversation's project space (双模式工作区). Files are the panel's always-on body;
 * this view injects workspace-level affordances into the files toolbar's single
 * header row (FileBrowser owns that row): 云端/本地选择器 (leading) plus 导出菜单 / 软删区
 * (trailing). The shell (SidePanel) owns the frame / resize / close.
 *
 * 快照不在此面板。云端命名版本在「我的文件」根右键「版本…」；
 * 右坞「改动」tab 只审本对话 AI 改动与回合基线回滚（不含留版本）。
 * 本机命名版本无产品 UI（盘上 `AgentCore/versions` / sidecar 仍在）。
 *
 * 交接（把活交给云端团队）已下沉为对话时间线里的「后台云端任务」卡（交接「方案 B」/
 * `BackgroundTaskCard`），完成后就地内联评审应用，不再占用工作区侧栏的独立入口。
 *
 * A draft conversation (no id yet) has no server workspace, so it shows an empty
 * hint until the first turn persists it.
 */
export function WorkspaceMode() {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const [trashOpen, setTrashOpen] = useState(false);
  const [exporting, setExporting] = useState(false);

  // 与文件中枢同一份数据 + 同一个解析器：对话→其工作区(WorkspaceInfo)→FileSource。本地走桌面
  // IPC、云端走 REST，故 Agent 在本地写的文件这里也能列出（修复「写在本地、读在云端」）。
  const ws = useConversationWorkspace(conversationId);
  const fsAvailable = hasLocalFiles();
  const source = useConversationFileSource(conversationId);

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
    return (
      <EmptyHint
        inline
        icon={<FolderOpen size={26} className="text-muted-foreground/40" />}
        title="云端草稿"
        hint="发送第一条消息后，快速对话产生的文件会出现在这里。"
      />
    );
  }

  const runExport = async (fn: () => Promise<unknown>) => {
    if (!conversationId || exporting) return;
    setExporting(true);
    try {
      await fn();
    } finally {
      setExporting(false);
    }
  };

  const emptyTreeHint = fsAvailable
    ? "工作区暂无文件。AI 产物会出现在这里；需要时可用工具栏「导出」或工作区菜单「合回到本机」。"
    : "工作区暂无文件。AI 产物会出现在这里；需要时可导出 ZIP。";

  // D2: shared-space mounts are cloud-execution only (local-bound chats have no
  // cross-runtime dual root).
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
      {/* 单行面板头：云端选择器（leading）+ 文件操作 + 导出 / 软删区（trailing）合到
          FilesSection 的工具栏一行（文件操作经其内部 FileTree 的 ref 驱动），不再单独占一行。 */}
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
                <>
                  <WorkspaceExportMenu
                    fsAvailable={fsAvailable}
                    exporting={exporting}
                    onExportFolder={() =>
                      void runExport(() =>
                        exportCloudDeskToPickedFolder(conversationId),
                      )
                    }
                    onExportZip={() =>
                      void runExport(() => exportCloudDeskZip(conversationId))
                    }
                  />
                  <IconButton title="软删区" onClick={() => setTrashOpen(true)}>
                    <Trash2 size={14} />
                  </IconButton>
                </>
              ) : localRootId ? (
                <IconButton title="软删区" onClick={() => setTrashOpen(true)}>
                  <Trash2 size={14} />
                </IconButton>
              ) : null}
            </>
          }
        />
      </div>

      <ExternalMountsSection conversationId={conversationId} />
      {isCloudWorkspace ? (
        <SharedMountsSection conversationId={conversationId} />
      ) : null}

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

function WorkspaceExportMenu({
  fsAvailable,
  exporting,
  onExportFolder,
  onExportZip,
}: {
  fsAvailable: boolean;
  exporting: boolean;
  onExportFolder: () => void;
  onExportZip: () => void;
}) {
  const [open, setOpen] = useState(false);
  const icon = exporting ? (
    <Loader2 size={14} className="animate-spin" />
  ) : fsAvailable ? (
    <FolderDown size={14} />
  ) : (
    <Download size={14} />
  );

  if (!fsAvailable) {
    return (
      <IconButton title="导出 ZIP" disabled={exporting} onClick={onExportZip}>
        {icon}
      </IconButton>
    );
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <IconButton
          title="导出"
          disabled={exporting}
          aria-label="导出"
          aria-expanded={open}
        >
          {icon}
        </IconButton>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-56 p-1.5">
        <Button
          variant="ghost"
          onClick={() => {
            setOpen(false);
            onExportFolder();
          }}
          className="h-auto w-full justify-start px-2.5 py-1.5 text-left text-xs font-medium"
        >
          <span className="min-w-0 flex-1">
            <span className="block truncate">导出到本机文件夹</span>
            <span className="block truncate text-xs font-normal text-muted-foreground">
              每次可选目录
            </span>
          </span>
        </Button>
        <Button
          variant="ghost"
          onClick={() => {
            setOpen(false);
            onExportZip();
          }}
          className="h-auto w-full justify-start px-2.5 py-1.5 text-left text-xs font-medium"
        >
          <span className="min-w-0 flex-1">
            <span className="block truncate">导出 ZIP</span>
            <span className="block truncate text-xs font-normal text-muted-foreground">
              下载云端快照拷贝
            </span>
          </span>
        </Button>
      </PopoverContent>
    </Popover>
  );
}
