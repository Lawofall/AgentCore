import { collectClipboardFiles } from "@/lib/clipboardFiles";
import {
  type Dispatch,
  type SetStateAction,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { trackAttachmentUpload } from "./attachmentUploads";
import type { PendingAttachment } from "./composerAttachments";
import {
  ATTACH_MAX_BYTES,
  OVERSIZE_REASON,
  describeFileAttachment,
  residentAttachmentForFile,
  safeBrowserFileName,
} from "./resideAttachment";
import {
  type AttachmentFolderHint,
  resolveFolderFromCitedRoot,
} from "./resolveAttachmentFolder";

/** Soft attach errors: auto-dismiss (Slack / Linear style), not sticky form validation. */
const DROP_ERROR_MS = 4000;

/**
 * 生成中不拦附加：发送路径本就支持带附件的插话 / 排队（``useComposerSend`` 的
 * mid-flight 分支会跑 ``settleAttachments``），拦住只会让粘贴 / 拖入静默失败。
 */
export function useComposerDrop(
  attachments: PendingAttachment[],
  setAttachments: Dispatch<SetStateAction<PendingAttachment[]>>,
  conversationId: string | null = null,
  onAttached?: (index: number) => void,
  onAttachmentFolderHint?: (hint: AttachmentFolderHint) => void,
) {
  const [dragOver, setDragOver] = useState(false);
  const [dropError, setDropError] = useState<string | null>(null);
  const dropErrorTimer = useRef<number | null>(null);
  const countRef = useRef(attachments.length);
  const inflightKeysRef = useRef(new Set<string>());

  useEffect(() => {
    countRef.current = attachments.length;
    inflightKeysRef.current = new Set(attachments.map((a) => a.key));
  }, [attachments]);

  const clearDropError = useCallback(() => {
    if (dropErrorTimer.current) {
      window.clearTimeout(dropErrorTimer.current);
      dropErrorTimer.current = null;
    }
    setDropError(null);
  }, []);

  const flashDropError = useCallback((msg: string) => {
    setDropError(msg);
    if (dropErrorTimer.current) window.clearTimeout(dropErrorTimer.current);
    dropErrorTimer.current = window.setTimeout(() => {
      dropErrorTimer.current = null;
      setDropError(null);
    }, DROP_ERROR_MS);
  }, []);

  // Timer lives in the hook: only clear on unmount. Do NOT wire this to a
  // recreated `drop` object in the parent — that cancelled auto-dismiss on every
  // setDropError re-render and left the red banner stuck.
  useEffect(() => {
    return () => {
      if (dropErrorTimer.current) window.clearTimeout(dropErrorTimer.current);
    };
  }, []);

  const patchAttachment = useCallback(
    (id: string, patch: Partial<PendingAttachment>) => {
      setAttachments((prev) =>
        prev.map((a) => (a.id === id ? { ...a, ...patch } : a)),
      );
    },
    [setAttachments],
  );

  /**
   * chip 先出、上传后台跑：用户拖入 / 粘贴后立刻看到附件，几 MB 的读盘与 PUT 不再
   * 卡在这次交互里，也不再推迟到点发送之后（那正是「点了完全没反应」的来源）。
   */
  const attachDroppedFile = useCallback(
    async (file: File) => {
      const key = `dropped:${file.name}:${file.size}`;
      if (
        inflightKeysRef.current.has(key) ||
        attachments.some((a) => a.key === key)
      ) {
        return;
      }
      if (file.size > ATTACH_MAX_BYTES) {
        flashDropError(OVERSIZE_REASON);
        return;
      }

      const id = crypto.randomUUID();
      const name = safeBrowserFileName(file.name);
      inflightKeysRef.current.add(key);
      const index = countRef.current;
      countRef.current += 1;
      setAttachments((prev) => {
        if (prev.some((a) => a.key === key)) return prev;
        return [
          ...prev,
          {
            id,
            key,
            name,
            path: name,
            text: "",
            truncated: false,
            kind: "file",
            // 先按 MIME 猜；读过头部字节后由 describeFileAttachment 修正。
            binary: file.type.startsWith("image/"),
            fileBlob: file,
            uploadState: "uploading",
          },
        ];
      });
      onAttached?.(index);

      // 预览元信息只读头部，先落到 chip 上——这样即便上传失败，发送时的重试也拿得到
      // 正确的正文 / 二进制判定，不会把 docx 当空文本发出去。
      let meta: Awaited<ReturnType<typeof describeFileAttachment>> | undefined;
      try {
        meta = await describeFileAttachment(file);
        patchAttachment(id, meta);
      } catch {
        /* 读头失败不致命：驻留时会再判一次 */
      }

      const res = await trackAttachmentUpload(
        id,
        conversationId,
        residentAttachmentForFile(conversationId, file, meta),
      );

      if (!res.ok) {
        flashDropError(res.reason);
        patchAttachment(id, { uploadState: "error", uploadError: res.reason });
        return;
      }
      patchAttachment(id, {
        name: res.name,
        path: res.path,
        text: res.text,
        truncated: res.truncated,
        binary: res.binary,
        workspacePath: res.workspacePath,
        stagingId: res.stagingId,
        citedRootId: res.citedRootId,
        citedRelPath: res.citedRelPath,
        // 已落地就放掉 File；只有还需发送时再传的草稿才继续持有。
        fileBlob: res.fileBlob,
        uploadState: undefined,
        uploadError: undefined,
      });
      if (!conversationId && onAttachmentFolderHint) {
        const cited =
          res.citedRootId && res.citedRelPath
            ? resolveFolderFromCitedRoot(res.citedRootId, res.citedRelPath)
            : null;
        if (cited) onAttachmentFolderHint(cited);
      }
    },
    [
      attachments,
      conversationId,
      flashDropError,
      onAttached,
      onAttachmentFolderHint,
      patchAttachment,
      setAttachments,
    ],
  );

  /** 多文件并行附加：串行 for-await 会让第 N 个文件白等前 N-1 个传完。 */
  const attachFiles = useCallback(
    async (files: readonly File[]) => {
      await Promise.all(files.map((f) => attachDroppedFile(f)));
    },
    [attachDroppedFile],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setDragOver(false);
  }, []);

  // 粘贴入框: Ctrl/Cmd+V 文件或截图 → 与 drop 同驻留链（桌面无 path 时 preload 走字节旁路）。
  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const files = collectClipboardFiles(e.clipboardData);
      if (files.length === 0) return;
      e.preventDefault();
      clearDropError();
      void attachFiles(files);
    },
    [attachFiles, clearDropError],
  );

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      if (!e.dataTransfer.types.includes("Files")) return;
      e.preventDefault();
      setDragOver(false);
      // New drop attempt: clear prior soft error so feedback matches this action.
      clearDropError();
      const dropped: File[] = [];
      let sawDir = false;
      const items = Array.from(e.dataTransfer.items ?? []);
      if (items.length) {
        for (const item of items) {
          if (item.kind !== "file") continue;
          if (item.webkitGetAsEntry?.()?.isDirectory) {
            sawDir = true;
            continue;
          }
          const f = item.getAsFile();
          if (f) dropped.push(f);
        }
      } else {
        dropped.push(...Array.from(e.dataTransfer.files));
      }
      if (sawDir) flashDropError("文件夹请用 @ 引用，拖拽仅支持文件");
      await attachFiles(dropped);
    },
    [attachFiles, clearDropError, flashDropError],
  );

  return {
    dragOver,
    dropError,
    clearDropError,
    attachDroppedFile,
    attachFiles,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    handlePaste,
  };
}
