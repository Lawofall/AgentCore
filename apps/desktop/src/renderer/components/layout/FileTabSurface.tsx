import { FileDetail } from "@/components/files/FileDetail";
import { EmptyHint } from "@/components/files/parts";
import { useFileTabSourceState } from "@/hooks/useConversationFileSource";
import { createDocumentSource } from "@/services/sources/documentSource";
import { createMemorySource } from "@/services/sources/memorySource";
import { useConversationStore } from "@/stores/conversation";
import type { FileTabChannel } from "@/stores/sidePanel";
import { FileText } from "lucide-react";
import { useMemo } from "react";

/**
 * File content-tab body for the docked SidePanel and float hosts.
 * Disk tabs resolve via {@link useFileTabSourceState}; entry tabs (memory /
 * document) use the same FileSource + FileDetail pair as the files page.
 */
export function FileTabSurface({
  path,
  name,
  workspaceId,
  channel,
  onClose,
}: {
  path: string;
  name: string;
  workspaceId?: string;
  channel?: FileTabChannel;
  onClose: () => void;
}) {
  const currentConversationId = useConversationStore(
    (s) => s.currentConversationId,
  );
  // Entry tabs don't need the conversation desk; skip so opening 设定 doesn't
  // wait on / 404 a workspace path that isn't theirs.
  const disk = useFileTabSourceState(
    channel ? null : currentConversationId,
    channel ? undefined : workspaceId,
  );
  const memorySource = useMemo(() => createMemorySource(), []);
  const documentSource = useMemo(() => createDocumentSource(), []);

  const source =
    channel === "memory"
      ? memorySource
      : channel === "document"
        ? documentSource
        : disk.source;
  const pending = channel ? false : disk.pending;

  if (!path || !name) {
    return (
      <EmptyHint
        inline
        icon={<FileText size={26} className="text-muted-foreground/40" />}
        title="打开文件"
        hint="在「工作区」文件树中点击文件，或点终稿里的路径——将在此显示为独立标签。"
      />
    );
  }
  if (!source) {
    return (
      <EmptyHint
        inline
        icon={<FileText size={26} className="text-muted-foreground/40" />}
        title={name}
        hint={pending ? "正在定位文件…" : "当前会话尚无可用文件源。"}
      />
    );
  }
  return (
    <FileDetail
      key={`${channel ?? ""}:${workspaceId ?? ""}:${path}`}
      source={source}
      path={path}
      name={name}
      onClose={onClose}
    />
  );
}
