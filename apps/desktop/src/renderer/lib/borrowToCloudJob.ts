/**
 * 「云上做完再写入」后台任务：复用 {@link runImportToCloud}，与导入共用
 * {@link useImportToCloudJobStore}（两路不能同时跑）。本机授权根始终
 * `ownsRoot: false`，留给之后写回。
 */
import { set as setBorrowPreference } from "@/lib/borrowOriginalPreference";
import {
  IMPORT_PUT_MAX_BYTES,
  ImportToCloudCancelledError,
  type ImportToCloudProgress,
  type ImportToCloudResult,
  formatImportToCloudProgress,
  runImportToCloud,
} from "@/lib/importToCloud";
import { setMergeLanding } from "@/lib/mergeLandingPreference";
import { openDraftConversation } from "@/lib/newConversation";
import { queryClient } from "@/lib/queryClient";
import { workspaceKeys } from "@/lib/queryKeys";
import { notifyInfo } from "@/lib/toast";
import { useFoldersStore } from "@/stores/folders";
import { useImportToCloudJobStore } from "@/stores/importToCloudJob";
import type { FsRoot } from "@shared/ipc-contract";
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";
import { createElement } from "react";
import { toast } from "sonner";

const TOAST_ID = "borrow-to-cloud";

const SUCCESS_TOAST_MS = 5_000;
const EXTENDED_TOAST_MS = 8_000;
const CANCELLED_PLAIN_TOAST_MS = 3_000;

const ORIGINAL_UNCHANGED = "电脑上的原件还没改";

function openFolderToastAction(folderId: string): {
  label: string;
  onClick: () => void;
} {
  return {
    label: "打开",
    onClick: () => openDraftConversation(folderId),
  };
}

const CLEAR_ACTION = { action: undefined as undefined };

const loadingIcon = createElement(Loader2, {
  size: 16,
  className: "animate-spin text-primary",
});
const successIcon = createElement(CheckCircle2, {
  size: 16,
  className: "text-success",
});
const warningIcon = createElement(AlertTriangle, {
  size: 16,
  className: "text-muted-foreground",
});
const errorIcon = createElement(AlertTriangle, {
  size: 16,
  className: "text-destructive",
});

export type StartBorrowToCloudJobOpts = {
  root: FsRoot;
  folderName: string;
  onBorrowed?: (folderId: string) => void;
};

export function formatBorrowToCloudToast(result: ImportToCloudResult): {
  message: string;
  description?: string;
} {
  if (!result.partial) {
    const uploadBit =
      result.uploaded > 0
        ? `已上传 ${result.uploaded} 个文件。`
        : "文件夹已创建（无文件可传）。";
    return {
      message: `已复制到云上「${result.folderName}」`,
      description: `${uploadBit}${ORIGINAL_UNCHANGED}。`,
    };
  }
  const bits: string[] = [];
  if (result.archiveTruncated) {
    bits.push("内容超过 100MiB 或 2 万个文件，只复制了一部分");
  }
  if (result.skippedOversized.length > 0) {
    bits.push(
      `跳过 ${result.skippedOversized.length} 个超过 ${IMPORT_PUT_MAX_BYTES / (1024 * 1024)}MiB 的文件`,
    )
  }
  return {
    message: `已复制到云上「${result.folderName}」（部分）`,
    description: `${bits.join("；")}。已上传 ${result.uploaded} 个文件。${ORIGINAL_UNCHANGED}。`,
  };
}

export function formatBorrowToCloudCancelledToast(
  err: ImportToCloudCancelledError,
): { message: string; description?: string } {
  if (err.folderId && err.folderName) {
    return {
      message: `已取消；文件夹「${err.folderName}」已保留`,
      description:
        "上传未完成，文件夹里的内容可能不全。可以稍后重试，或自行删除。",
    };
  }
  return { message: "已取消" };
}

function showProgressToast(p: ImportToCloudProgress): void {
  toast(formatImportToCloudProgress(p) || "正在复制到云上…", {
    id: TOAST_ID,
    duration: Number.POSITIVE_INFINITY,
    icon: loadingIcon,
    action: {
      label: "取消",
      onClick: () => useImportToCloudJobStore.getState().cancel(),
    },
  });
}

function successDuration(result: ImportToCloudResult): number {
  return result.partial ? EXTENDED_TOAST_MS : SUCCESS_TOAST_MS;
}

function rememberBorrowLanding(folderId: string, root: FsRoot): void {
  setMergeLanding({ kind: "folder", folderId }, root.id);
  setBorrowPreference(folderId, {
    rootId: root.id,
    originalName: root.name,
    promoted: false,
  });
  useFoldersStore.getState().setDraftWorkspaceIntent({
    kind: "folder",
    folderId,
  });
  openDraftConversation(folderId);
}

function notifyBusy(): void {
  notifyInfo("已有上传正在进行", {
    description: "请等待当前上传完成，或在进度提示中取消后再试",
  });
}

/**
 * Start background copy-to-cloud. Returns false (and tips) when a job
 * — import or this path — is already running. Always keeps the authorized root.
 */
export function startBorrowToCloudJob(
  opts: StartBorrowToCloudJobOpts,
): boolean {
  const store = useImportToCloudJobStore.getState();
  if (store.isRunning()) {
    notifyBusy();
    return false;
  }

  const controller = new AbortController();
  if (!store.begin(controller)) {
    notifyBusy();
    return false;
  }

  showProgressToast({ phase: "archiving" });

  void (async () => {
    try {
      const result = await runImportToCloud({
        root: opts.root,
        ownsRoot: false,
        folderName: opts.folderName,
        signal: controller.signal,
        onProgress: showProgressToast,
      });
      rememberBorrowLanding(result.folderId, opts.root);
      const done = formatBorrowToCloudToast(result);
      toast.success(done.message, {
        id: TOAST_ID,
        description: done.description,
        icon: successIcon,
        duration: successDuration(result),
        action: openFolderToastAction(result.folderId),
      });
      void queryClient.invalidateQueries({ queryKey: workspaceKeys.list });
      opts.onBorrowed?.(result.folderId);
    } catch (e) {
      if (e instanceof ImportToCloudCancelledError) {
        const cancelled = formatBorrowToCloudCancelledToast(e);
        if (e.folderId) {
          rememberBorrowLanding(e.folderId, opts.root);
          toast.warning(cancelled.message, {
            id: TOAST_ID,
            description: cancelled.description,
            icon: warningIcon,
            duration: EXTENDED_TOAST_MS,
            action: openFolderToastAction(e.folderId),
          });
          void queryClient.invalidateQueries({ queryKey: workspaceKeys.list });
          opts.onBorrowed?.(e.folderId);
        } else {
          toast(cancelled.message, {
            id: TOAST_ID,
            icon: warningIcon,
            duration: CANCELLED_PLAIN_TOAST_MS,
            ...CLEAR_ACTION,
          });
        }
        return;
      }
      const detail =
        e instanceof Error ? e.message : typeof e === "string" ? e : "";
      toast.error("复制到云上失败", {
        id: TOAST_ID,
        description: detail || undefined,
        icon: errorIcon,
        duration: EXTENDED_TOAST_MS,
        ...CLEAR_ACTION,
      });
    } finally {
      useImportToCloudJobStore.getState().end(controller);
    }
  })();

  return true;
}

export function isBorrowToCloudJobRunning(): boolean {
  return useImportToCloudJobStore.getState().isRunning();
}

export function cancelBorrowToCloudJob(): void {
  useImportToCloudJobStore.getState().cancel();
}
