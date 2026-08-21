import type { FileNode } from "@/api/workspace";
import { Modal } from "@/components/Modal";
import type { FileBrowserOps } from "@/components/fileBrowser/ops";
import {
  entryNameError,
  joinPath,
  moveTargetError,
  parentDir,
} from "@/components/fileBrowser/paths";
import {
  canonicalBrowseDir,
  displayDirName,
  presentDirLabel,
  presentPathLabel,
  workroomChildren,
} from "@/lib/stageDirs";
// 一个条目的管理动作：重命名 / 移动 / 删除（云工作区可写）。
//
// 一个 sheet 起头，按选择进到各自的对话框——手机屏放不下常驻的行内按钮，长按也没有可见
// 提示，所以入口是行尾的「⋯」。内容编辑不在这里：它在预览页顶栏，那里才看得见正文
// （避免同一能力两套判据——列表按扩展名猜、预览按真实内容判）。
import { ChevronRight, Folder } from "lucide-react";
import { useState } from "react";

/** A finished action, as the browser needs it: what to re-check, and what to tell the user. */
export interface EntryChange {
  /** The path that changed — a preview of it (or of anything under it) is now stale. */
  from: string;
  message: string;
}

type Stage = "menu" | "rename" | "move" | "delete";

export function FileEntryActions({
  entry,
  ops,
  tree,
  onClose,
  onDone,
}: {
  entry: FileNode;
  ops: FileBrowserOps;
  /** `dir → children`, reused for the move picker's folder navigation (no refetch). */
  tree: Map<string, FileNode[]>;
  onClose: () => void;
  onDone: (change: EntryChange) => void;
}) {
  const [stage, setStage] = useState<Stage>("menu");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const kindWord = entry.isDir ? "文件夹" : "文件";

  const run = async (op: () => Promise<void>, change: EntryChange) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await op();
      onDone(change);
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
      setBusy(false);
    }
  };

  if (stage === "rename") {
    return (
      <RenameEntryDialog
        entry={entry}
        busy={busy}
        error={error}
        onClose={onClose}
        onSave={(name) => {
          const dst = joinPath(parentDir(entry.path), name);
          if (dst === entry.path) {
            onClose();
            return;
          }
          void run(() => ops.move(entry.path, dst), {
            from: entry.path,
            message: `已重命名为「${name}」`,
          });
        }}
      />
    );
  }

  if (stage === "move") {
    return (
      <MoveTargetPicker
        entry={entry}
        tree={tree}
        busy={busy}
        error={error}
        onClose={onClose}
        onPick={(dir) => {
          const dst = joinPath(dir, entry.name);
          void run(() => ops.move(entry.path, dst), {
            from: entry.path,
            message: `已移动到「${presentDirLabel(dir)}」`,
          });
        }}
      />
    );
  }

  if (stage === "delete") {
    return (
      <Modal className="dialog" onClose={onClose} label="删除确认">
        <div className="dialog-title">删除「{entry.name}」？</div>
        <div className="dialog-msg">
          {entry.isDir
            ? "文件夹和里面的内容会移到软删区，可在「软删区」还原。"
            : "文件会移到软删区，可在「软删区」还原。"}
        </div>
        {error && <p className="error hint">{error}</p>}
        <div className="dialog-actions">
          <button
            type="button"
            className="link"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </button>
          <button
            type="button"
            className="dialog-danger"
            disabled={busy}
            onClick={() =>
              void run(() => ops.remove(entry.path), {
                from: entry.path,
                message: `已删除「${entry.name}」，可在软删区还原`,
              })
            }
          >
            {busy ? "删除中…" : "删除"}
          </button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal className="sheet" onClose={onClose} label={`${kindWord}操作`}>
      <div className="sheet-title">{entry.name}</div>
      <button
        type="button"
        className="sheet-item"
        onClick={() => setStage("rename")}
      >
        重命名
      </button>
      <button
        type="button"
        className="sheet-item"
        onClick={() => setStage("move")}
      >
        移动到…
      </button>
      <button
        type="button"
        className="sheet-item sheet-danger"
        onClick={() => setStage("delete")}
      >
        删除
      </button>
      <button
        type="button"
        className="sheet-item sheet-cancel"
        onClick={onClose}
      >
        取消
      </button>
    </Modal>
  );
}

function RenameEntryDialog({
  entry,
  busy,
  error,
  onClose,
  onSave,
}: {
  entry: FileNode;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSave: (name: string) => void;
}) {
  const [name, setName] = useState(entry.name);
  const nameError = entryNameError(name);
  const submit = () => {
    if (busy || nameError) return;
    onSave(name.trim());
  };
  return (
    <Modal className="dialog" onClose={onClose} label="重命名">
      <div className="dialog-title">
        重命名{entry.isDir ? "文件夹" : "文件"}
      </div>
      <input
        className="dialog-input"
        value={name}
        // biome-ignore lint/a11y/noAutofocus: a rename dialog should focus its field
        autoFocus
        aria-label="新名称"
        placeholder="名称"
        disabled={busy}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
        }}
      />
      {name.trim() && nameError && <p className="error hint">{nameError}</p>}
      {error && <p className="error hint">{error}</p>}
      <div className="dialog-actions">
        <button
          type="button"
          className="link"
          disabled={busy}
          onClick={onClose}
        >
          取消
        </button>
        <button type="button" disabled={busy || !!nameError} onClick={submit}>
          {busy ? "保存中…" : "保存"}
        </button>
      </div>
    </Modal>
  );
}

/**
 * Pick a destination folder by walking the already-loaded tree.
 *
 * A phone has no room for drag-and-drop between panes, so「移动到…」is a
 * full-screen folder walk ending in 「移动到这里」. The tree is already in memory,
 * so navigating costs nothing; only the move itself hits the network.
 */
function MoveTargetPicker({
  entry,
  tree,
  busy,
  error,
  onClose,
  onPick,
}: {
  entry: FileNode;
  tree: Map<string, FileNode[]>;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onPick: (dir: string) => void;
}) {
  const [dir, setDir] = useState(canonicalBrowseDir(parentDir(entry.path)));
  const folders = workroomChildren(tree, dir).filter(
    (n) => n.isDir && n.path !== entry.path,
  );
  const blocked = moveTargetError(entry, dir);

  return (
    <Modal className="viewer" onClose={onClose} label="选择目标文件夹">
      <header className="bar viewer-bar">
        <button
          type="button"
          className="link"
          disabled={busy}
          onClick={() =>
            dir === "" ? onClose() : setDir(canonicalBrowseDir(parentDir(dir)))
          }
        >
          {dir === "" ? "取消" : "← 上一级"}
        </button>
        <span className="viewer-name">移动「{entry.name}」</span>
        <span className="bar-right" aria-hidden />
      </header>

      <div className="move-picker-crumb muted">
        目标：{presentPathLabel(dir)}
      </div>

      <div className="list file-list">
        {folders.length === 0 ? (
          <p className="muted hint">这里没有子文件夹。可直接移动到此处。</p>
        ) : (
          folders.map((f) => (
            <button
              key={f.path}
              type="button"
              className="file-row"
              aria-label={`进入文件夹 ${displayDirName(f.path, f.name)}`}
              disabled={busy}
              onClick={() => setDir(canonicalBrowseDir(f.path))}
            >
              <span className="file-icon" aria-hidden>
                <Folder size={16} />
              </span>
              <span className="file-row-main">
                <span className="file-name">
                  {displayDirName(f.path, f.name)}
                </span>
              </span>
              <span className="file-chevron" aria-hidden>
                <ChevronRight size={18} />
              </span>
            </button>
          ))
        )}
      </div>

      <div className="move-picker-actions">
        {blocked && <p className="muted hint">{blocked}</p>}
        {error && <p className="error hint">{error}</p>}
        <button
          type="button"
          className="move-picker-confirm"
          disabled={busy || !!blocked}
          onClick={() => onPick(dir)}
        >
          {busy ? "移动中…" : `移动到「${presentDirLabel(dir)}」`}
        </button>
      </div>
    </Modal>
  );
}
