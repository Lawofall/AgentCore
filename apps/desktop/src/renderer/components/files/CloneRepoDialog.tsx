import { Button, Input } from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { addFolderCache } from "@/hooks/useFolders";
import { queryClient } from "@/lib/queryClient";
import { workspaceKeys } from "@/lib/queryKeys";
import { notifyActionError, notifySuccess } from "@/lib/toast";
import { ApiError } from "@/services/api";
import { createFolder } from "@/services/folders";
import { wsCloneRepo } from "@/services/workspaces";
import { useFoldersStore } from "@/stores/folders";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

/**
 * AppShell host for Composer / 命令面板「连接 Git」——复用 {@link CloneRepoDialog}
 * + ``wsCloneRepo``（G3），与文件工作台克隆同一条链路。
 */
export function ConnectGitDialogHost() {
  const open = useFoldersStore((s) => s.connectGitOpen);
  const wsId = useFoldersStore((s) => s.connectGitWsId);
  const close = useFoldersStore((s) => s.closeConnectGit);

  return (
    <CloneRepoDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      wsId={wsId}
    />
  );
}

/** Default project / dest name: last URL path segment minus `.git` (align server G3). */
export function deriveRepoNameFromUrl(repoUrl: string): string {
  try {
    const path = new URL(repoUrl.trim()).pathname.replace(/\/+$/, "");
    const seg = path.split("/").pop() ?? "";
    const name = seg.endsWith(".git") ? seg.slice(0, -4) : seg;
    return name || "repo";
  } catch {
    return "repo";
  }
}

/**
 * Cloud-workspace shallow clone dialog (G3). Calls
 * ``POST /v1/workspaces/{ws_id}/clone`` — http(s) only; private repos need
 * account PAT under 设置 → Git 凭据.
 *
 * When ``wsId`` is null, creates a cloud folder first（从 Git 克隆 = 云 clone
 * remote），then clones into ``folder:{id}``.
 */
export function CloneRepoDialog({
  open,
  onOpenChange,
  wsId,
  onCloned,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Existing cloud ws (`folder:…` / `conv:…`). Null → create cloud folder. */
  wsId: string | null;
  onCloned?: (path: string, folderId?: string) => void;
}) {
  const [repoUrl, setRepoUrl] = useState("");
  const [dest, setDest] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** Survives a failed clone so retry does not create another empty project. */
  const [createdFolderId, setCreatedFolderId] = useState<string | null>(null);
  const needsNewFolder = wsId == null;

  const reset = () => {
    setRepoUrl("");
    setDest("");
    setError(null);
    setBusy(false);
    setCreatedFolderId(null);
  };

  const submit = async () => {
    const url = repoUrl.trim();
    if (!url) {
      setError("请填写仓库地址");
      return;
    }
    if (!/^https?:\/\//i.test(url)) {
      setError("仅支持 http(s) 地址（不支持 ssh / file）");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      let targetWsId = wsId;
      let folderId: string | undefined = createdFolderId ?? undefined;
      const destTrim = dest.trim();
      if (!targetWsId) {
        if (!folderId) {
          const folderName = destTrim || deriveRepoNameFromUrl(url);
          const { folder } = await createFolder({
            name: folderName,
            mode: "cloud",
          });
          addFolderCache(folder);
          folderId = folder.id;
          setCreatedFolderId(folder.id);
          useFoldersStore.getState().setDraftWorkspaceIntent({
            kind: "folder",
            folderId: folder.id,
          });
        }
        targetWsId = `folder:${folderId}`;
      }
      const path = await wsCloneRepo(targetWsId, {
        repoUrl: url,
        dest: destTrim || null,
      });
      notifySuccess(`已克隆到 ${path}`);
      void queryClient.invalidateQueries({ queryKey: workspaceKeys.list });
      onCloned?.(path, folderId);
      onOpenChange(false);
      reset();
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? (e.serverMessage ?? "克隆失败")
          : "克隆失败，请重试";
      setError(msg);
      if (/凭据|PAT|401|403|Authentication|authentication/i.test(msg)) {
        notifyActionError("私仓需要凭据", e);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>
            {needsNewFolder ? "从 Git 克隆" : "克隆仓库"}
          </DialogTitle>
          <DialogDescription>
            {needsNewFolder
              ? "浅克隆到「我的文件」里的新文件夹（仅 http(s)）。私仓请先在 "
              : "浅克隆到当前云工作区（仅 http(s)）。私仓请先在 "}
            <Link
              to="/more/git"
              className="text-foreground underline underline-offset-2"
              onClick={() => onOpenChange(false)}
            >
              设置 → Git 凭据
            </Link>{" "}
            配置 PAT。
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3 py-2">
          <div className="space-y-1.5">
            <label className="text-xs font-medium" htmlFor="clone-url">
              仓库 URL
            </label>
            <Input
              id="clone-url"
              placeholder="https://github.com/owner/repo.git"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              disabled={busy}
              autoFocus
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium" htmlFor="clone-dest">
              {needsNewFolder
                ? "文件夹名称 / 目标目录（可选）"
                : "目标目录（可选）"}
            </label>
            <Input
              id="clone-dest"
              placeholder="默认取仓库名"
              value={dest}
              onChange={(e) => setDest(e.target.value)}
              disabled={busy}
            />
          </div>
          {error && (
            <p className="text-sm text-muted-foreground" role="alert">
              {error}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => onOpenChange(false)}
          >
            取消
          </Button>
          <Button disabled={busy} onClick={() => void submit()}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : null}
            克隆
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
