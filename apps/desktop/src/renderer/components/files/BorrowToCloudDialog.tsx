import { Button, Input } from "@/components/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { pickLocalFolderRoot } from "@/lib/bindLocalFolder";
import {
  isBorrowToCloudJobRunning,
  startBorrowToCloudJob,
} from "@/lib/borrowToCloudJob";
import { notifyInfo } from "@/lib/toast";
import { type ImportToCloudPrefill, useFoldersStore } from "@/stores/folders";
import type { FsRoot } from "@shared/ipc-contract";
import { FolderOpen } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

/**
 * AppShell host for「云上做完再写入」——与 {@link ImportToCloudDialogHost} 同构：
 * store 开关 + Dialog。不改导入对话框文案。
 */
export function BorrowToCloudDialogHost() {
  const open = useFoldersStore((s) => s.borrowToCloudOpen);
  const prefill = useFoldersStore((s) => s.borrowToCloudPrefill);
  const close = useFoldersStore((s) => s.closeBorrowToCloud);

  return (
    <BorrowToCloudDialog
      open={open}
      prefill={prefill}
      onOpenChange={(next) => {
        if (!next) close();
      }}
    />
  );
}

type OwnedRoot = { root: FsRoot; owns: boolean };

/**
 * 选本机夹 → 可改云上文件夹名 → 关窗后后台复制。授权根留下，不 removeRoot。
 * Composer 已选路过时带 prefill；ownsRoot 的才在取消时 removeRoot。
 */
export function BorrowToCloudDialog({
  open,
  onOpenChange,
  onBorrowed,
  prefill = null,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onBorrowed?: (folderId: string) => void;
  prefill?: ImportToCloudPrefill | null;
}) {
  const [folderName, setFolderName] = useState("");
  const [owned, setOwned] = useState<OwnedRoot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const prefillAppliedForOpen = useRef(false);
  const ownedRef = useRef<OwnedRoot | null>(null);

  const setOwnedSync = useCallback((next: OwnedRoot | null) => {
    ownedRef.current = next;
    setOwned(next);
  }, []);

  const root = owned?.root ?? null;

  const dropIfOwned = async (prev: OwnedRoot | null) => {
    if (!prev?.owns) return;
    try {
      await window.fsApi?.removeRoot?.(prev.root.id);
    } catch {
      // ignore — cancel-time leak is non-fatal
    }
  };

  const reset = () => {
    const prev = ownedRef.current;
    ownedRef.current = null;
    setFolderName("");
    setOwned(null);
    setError(null);
    prefillAppliedForOpen.current = false;
    void dropIfOwned(prev);
  };

  useEffect(() => {
    if (!open) {
      prefillAppliedForOpen.current = false;
      return;
    }
    if (prefillAppliedForOpen.current) return;
    prefillAppliedForOpen.current = true;

    const nameHint = prefill?.folderName?.trim() || "";
    if (nameHint) setFolderName(nameHint);

    const rootId = prefill?.rootId?.trim();
    if (!rootId) return;

    let cancelled = false;
    void (async () => {
      try {
        const roots = (await window.fsApi?.listRoots?.()) ?? [];
        if (cancelled) return;
        const found = roots.find((r) => r.id === rootId);
        if (found) {
          setOwnedSync({ root: found, owns: Boolean(prefill?.ownsRoot) });
          if (!nameHint) setFolderName(found.name);
        }
      } catch {
        // Prefill miss → user still picks manually.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, prefill, setOwnedSync]);

  const pickFolder = async () => {
    setError(null);
    const picked = await pickLocalFolderRoot();
    if (!picked.ok) {
      if (picked.reason === "cancelled") return;
      setError(picked.message);
      return;
    }
    const prev = ownedRef.current;
    setOwnedSync({ root: picked.root, owns: true });
    void dropIfOwned(prev);
    if (!folderName.trim()) {
      setFolderName(picked.root.name);
    }
  };

  const requestClose = () => {
    reset();
    onOpenChange(false);
  };

  const submit = () => {
    const selected = ownedRef.current;
    if (!selected) {
      setError("请先选择本机文件夹");
      return;
    }
    if (isBorrowToCloudJobRunning()) {
      notifyInfo("已有上传正在进行", {
        description: "请等待当前上传完成，或在进度提示中取消后再试",
      });
      return;
    }
    const name = folderName.trim() || selected.root.name;
    // Keep the authorized root for write-back; null before close so reset
    // does not removeRoot.
    ownedRef.current = null;
    setOwned(null);
    setError(null);
    setFolderName("");
    prefillAppliedForOpen.current = false;
    onOpenChange(false);

    startBorrowToCloudJob({
      root: selected.root,
      folderName: name,
      onBorrowed,
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>云上做完再写入</DialogTitle>
          <DialogDescription>
            把选中的本机文件夹复制到云上做这一单。电脑上的原件先不动；做完再决定写不写回。这不是打开本机文件夹直接改。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 px-5 py-2">
          <div className="space-y-1.5">
            <label className="text-xs font-medium" htmlFor="borrow-folder">
              本机文件夹
            </label>
            <div className="flex gap-2">
              <Button
                id="borrow-folder"
                type="button"
                variant="neutral"
                className="min-w-0 flex-1 justify-start gap-2"
                onClick={() => void pickFolder()}
              >
                <FolderOpen size={14} className="shrink-0" />
                <span className="truncate">
                  {root ? root.name : "选择文件夹…"}
                </span>
              </Button>
            </div>
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium" htmlFor="borrow-name">
              云上的文件夹名称
            </label>
            <Input
              id="borrow-name"
              placeholder="默认取本机文件夹名"
              value={folderName}
              onChange={(e) => setFolderName(e.target.value)}
            />
          </div>
          {error ? (
            <p className="text-sm text-muted-foreground" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="neutral" onClick={() => requestClose()}>
            取消
          </Button>
          <Button disabled={!root} onClick={() => submit()}>
            开始
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
