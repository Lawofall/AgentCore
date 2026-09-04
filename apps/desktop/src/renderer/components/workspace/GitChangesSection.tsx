/**
 * U2/U3：「改动」tab 内 Git 轨 —— staged/unstaged 列表 + stage/commit/push/pull/fetch。
 * 与回合 zip 轨正交；冲突仅诚实横幅 + 打开文件（否决三方 merge UI）。
 */
import { Button, Textarea } from "@/components/ui";
import type { PresentGitRepoStatus } from "@/lib/gitRepoStatus";
import {
  deleteUntrackedFiles,
  gitCommit,
  gitDiffText,
  gitDiscard,
  gitFetch,
  gitPull,
  gitPush,
  gitStage,
  gitUnstage,
} from "@/lib/gitScm";
import { repoPathToWorkspaceRel } from "@/lib/repoPathToWorkspaceRel";
import { notifyInfo } from "@/lib/toast";
import { useSidePanelStore } from "@/stores/sidePanel";
import type { GitChangeEntry } from "@shared/ipc-contract";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  ChevronDown,
  ChevronRight,
  GitBranch,
  Loader2,
  Minus,
  Plus,
  RefreshCw,
  Trash2,
  Undo2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

/** 未暂存条目超过此数时，目录分组默认折叠。 */
const COLLAPSE_DIRS_THRESHOLD = 30;

/** Hover-only chrome: out of flow when idle so the path keeps the row. */
const hoverOnlyActionsClass =
  "hidden shrink-0 items-center gap-0.5 group-hover:flex group-focus-within:flex";

function basename(path: string): string {
  const norm = path.replace(/\\/g, "/");
  const i = norm.lastIndexOf("/");
  return i >= 0 ? norm.slice(i + 1) : norm;
}

function splitRepoPath(path: string): { dir: string; name: string } {
  const norm = path.replace(/\\/g, "/");
  const i = norm.lastIndexOf("/");
  if (i < 0) return { dir: "", name: norm };
  return { dir: norm.slice(0, i), name: norm.slice(i + 1) };
}

/** 分组标题短路径：末 1–2 段；空 = 仓根。完整路径用 title。 */
export function shortDirLabel(dir: string): string {
  if (!dir) return "仓根";
  const parts = dir.replace(/\\/g, "/").split("/").filter(Boolean);
  if (parts.length <= 2) return parts.join("/");
  return parts.slice(-2).join("/");
}

/** Porcelain XY → 主状态字母（列表侧已拆成 staged/unstaged）。 */
export function primaryStatusChar(code: string): string {
  const c = (code.length >= 2 ? code : `${code} `).slice(0, 2);
  if (c === "??") return "?";
  if (c[0] !== " ") return c[0];
  if (c[1] !== " ") return c[1];
  return (code.trim()[0] ?? "·").toUpperCase();
}

/** 行业 SCM：M 警示色 / A·? 成功色 / D 破坏色。 */
export function statusCharClass(ch: string): string {
  switch (ch) {
    case "M":
      return "text-warning";
    case "A":
    case "?":
      return "text-success";
    case "D":
      return "text-destructive";
    case "R":
    case "C":
      return "text-primary";
    case "U":
      return "text-warning";
    default:
      return "text-muted-foreground";
  }
}

/** 折叠行摘要顺序：常见改动类型优先。 */
const STATUS_SUMMARY_ORDER = ["M", "A", "D", "R", "C", "U", "?"] as const;

/** 按主状态字母聚合计数，供「未暂存 · N」旁摘要。 */
export function statusSummaryParts(
  entries: GitChangeEntry[],
): { ch: string; n: number }[] {
  const counts = new Map<string, number>();
  for (const e of entries) {
    const ch = primaryStatusChar(e.code);
    counts.set(ch, (counts.get(ch) ?? 0) + 1);
  }
  const parts: { ch: string; n: number }[] = [];
  const seen = new Set<string>();
  for (const ch of STATUS_SUMMARY_ORDER) {
    const n = counts.get(ch);
    if (n) {
      parts.push({ ch, n });
      seen.add(ch);
    }
  }
  for (const [ch, n] of counts) {
    if (!seen.has(ch)) parts.push({ ch, n });
  }
  return parts;
}

function StatusSummary({ entries }: { entries: GitChangeEntry[] }) {
  const parts = statusSummaryParts(entries);
  if (parts.length === 0) return null;
  return (
    <span className="ml-1.5 min-w-0 truncate tabular-nums text-xs">
      {parts.map((p, i) => (
        <span key={p.ch}>
          {i > 0 ? <span className="text-muted-foreground/40"> · </span> : null}
          <span className={statusCharClass(p.ch)}>
            {p.ch}
            {p.n}
          </span>
        </span>
      ))}
    </span>
  );
}

/** 仅未暂存已跟踪文件可 discard（未跟踪需 clean，产品禁）。 */
export function canDiscardChange(
  entry: GitChangeEntry,
  staged: boolean,
): boolean {
  if (staged) return false;
  return primaryStatusChar(entry.code) !== "?";
}

export function isUntrackedChange(entry: GitChangeEntry): boolean {
  return primaryStatusChar(entry.code) === "?";
}

/** 按父目录分组（仓根文件 dir=""）；目录按 localeCompare，根文件置顶。 */
export function groupGitChangesByDir(
  entries: GitChangeEntry[],
): { dir: string; entries: GitChangeEntry[] }[] {
  const map = new Map<string, GitChangeEntry[]>();
  for (const e of entries) {
    const { dir } = splitRepoPath(e.path);
    const list = map.get(dir);
    if (list) list.push(e);
    else map.set(dir, [e]);
  }
  const dirs = [...map.keys()].sort((a, b) => {
    if (a === "") return -1;
    if (b === "") return 1;
    return a.localeCompare(b);
  });
  return dirs.map((dir) => ({
    dir,
    entries: map.get(dir) ?? [],
  }));
}

function parseUnifiedDiff(
  text: string,
): { type: "add" | "del" | "context"; text: string }[] {
  const rows: { type: "add" | "del" | "context"; text: string }[] = [];
  for (const raw of text.split(/\r?\n/)) {
    if (
      raw.startsWith("diff ") ||
      raw.startsWith("index ") ||
      raw.startsWith("--- ") ||
      raw.startsWith("+++ ") ||
      raw.startsWith("@@")
    ) {
      continue;
    }
    if (raw.startsWith("+")) {
      rows.push({ type: "add", text: raw.slice(1) });
    } else if (raw.startsWith("-")) {
      rows.push({ type: "del", text: raw.slice(1) });
    } else if (raw.startsWith(" ") || raw === "") {
      rows.push({
        type: "context",
        text: raw.startsWith(" ") ? raw.slice(1) : raw,
      });
    } else {
      rows.push({ type: "context", text: raw });
    }
  }
  return rows;
}

function DiffPreview({ text }: { text: string }) {
  const rows = parseUnifiedDiff(text);
  if (rows.length === 0) {
    return <p className="px-2 py-1 text-xs text-muted-foreground">无差异</p>;
  }
  return (
    <div className="max-h-72 overflow-auto rounded-lg border border-border/60 font-mono text-xs leading-relaxed">
      {rows.map((l, i) => (
        <div
          // biome-ignore lint/suspicious/noArrayIndexKey: positional diff rows
          key={i}
          className={`flex ${
            l.type === "add"
              ? "bg-success/10 text-foreground"
              : l.type === "del"
                ? "bg-destructive/10 text-foreground"
                : "text-muted-foreground"
          }`}
        >
          <span className="w-5 shrink-0 select-none text-center text-muted-foreground/50">
            {l.type === "add" ? "+" : l.type === "del" ? "-" : " "}
          </span>
          <span className="whitespace-pre-wrap break-words pr-2">
            {l.text || " "}
          </span>
        </div>
      ))}
    </div>
  );
}

function ChangeRow({
  entry,
  staged,
  rootId,
  subpath,
  onMutated,
  onOpenFile,
  hideDir = false,
}: {
  entry: GitChangeEntry;
  staged: boolean;
  rootId: string;
  subpath: string;
  onMutated: () => void;
  onOpenFile: (repoPath: string) => void;
  /** 目录分组标题已展示时隐藏行内目录。 */
  hideDir?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [diff, setDiff] = useState<string | null>(null);
  const [loadingDiff, setLoadingDiff] = useState(false);
  const [busy, setBusy] = useState(false);
  const { dir, name } = splitRepoPath(entry.path);
  const statusCh = primaryStatusChar(entry.code);
  const untracked = isUntrackedChange(entry);
  const showDiscard = canDiscardChange(entry, staged);

  const toggleDiff = useCallback(async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (diff != null) return;
    setLoadingDiff(true);
    const text = await gitDiffText(rootId, entry.path, staged);
    setDiff(text ?? "");
    setLoadingDiff(false);
  }, [open, diff, rootId, entry.path, staged]);

  const onToggleStage = useCallback(async () => {
    setBusy(true);
    const ok = staged
      ? await gitUnstage(rootId, [entry.path])
      : await gitStage(rootId, [entry.path]);
    setBusy(false);
    if (ok) onMutated();
  }, [staged, rootId, entry.path, onMutated]);

  const onDiscard = useCallback(async () => {
    setBusy(true);
    const ok = await gitDiscard(rootId, entry.path);
    setBusy(false);
    if (ok) onMutated();
  }, [rootId, entry.path, onMutated]);

  const onDeleteUntracked = useCallback(async () => {
    const wsRel = repoPathToWorkspaceRel(entry.path, subpath);
    if (wsRel == null || wsRel === "") {
      notifyInfo("该文件不在当前工作区内", { description: entry.path });
      return;
    }
    setBusy(true);
    const ok = await deleteUntrackedFiles(rootId, [wsRel]);
    setBusy(false);
    if (ok) onMutated();
  }, [rootId, entry.path, subpath, onMutated]);

  return (
    <div>
      <div className="group flex h-6 items-center gap-0.5 px-1.5 hover:bg-muted/50">
        <button
          type="button"
          className="flex size-5 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={() => void toggleDiff()}
          aria-expanded={open}
          aria-label={open ? "收起差异" : "展开差异"}
          title={open ? "收起差异" : "展开差异"}
        >
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
        <span
          className={`w-3.5 shrink-0 text-center font-mono text-xs font-medium leading-none ${statusCharClass(statusCh)}`}
          title={entry.code}
        >
          {statusCh}
        </span>
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-1 text-left text-xs"
          onClick={() => onOpenFile(entry.path)}
          title={entry.path}
        >
          <span className="min-w-0 truncate text-foreground">{name}</span>
          {!hideDir && dir ? (
            <span className="min-w-0 truncate text-xs text-muted-foreground/60">
              {shortDirLabel(dir)}
            </span>
          ) : null}
        </button>
        {!staged && untracked ? (
          <span className={hoverOnlyActionsClass}>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-5 shrink-0 px-1"
              disabled={busy}
              onClick={() => void onDeleteUntracked()}
              aria-label="删除未跟踪文件"
              title="移入系统回收站"
            >
              <Trash2 size={12} className="text-muted-foreground" />
            </Button>
          </span>
        ) : showDiscard ? (
          <span className={hoverOnlyActionsClass}>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-5 shrink-0 px-1"
              disabled={busy}
              onClick={() => void onDiscard()}
              aria-label="丢弃改动"
              title="丢弃未暂存改动"
            >
              <Undo2 size={12} className="text-muted-foreground" />
            </Button>
          </span>
        ) : null}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-5 shrink-0 px-1 opacity-70 group-hover:opacity-100"
          disabled={busy}
          onClick={() => void onToggleStage()}
          aria-label={staged ? "取消暂存" : "暂存"}
          title={staged ? "取消暂存" : "暂存"}
        >
          {busy ? (
            <Loader2 size={12} className="animate-spin" />
          ) : staged ? (
            <Minus size={12} />
          ) : (
            <Plus size={12} />
          )}
        </Button>
      </div>
      {open && (
        <div className="px-2 pb-1.5 pl-7">
          {loadingDiff ? (
            <p className="text-xs text-muted-foreground">读取 diff…</p>
          ) : (
            <DiffPreview text={diff ?? ""} />
          )}
        </div>
      )}
    </div>
  );
}

function ChangeGroupList({
  entries,
  staged,
  rootId,
  subpath,
  onMutated,
  onOpenFile,
  keyPrefix,
}: {
  entries: GitChangeEntry[];
  staged: boolean;
  rootId: string;
  subpath: string;
  onMutated: () => void;
  onOpenFile: (repoPath: string) => void;
  keyPrefix: string;
}) {
  const groups = groupGitChangesByDir(entries);
  const multiGroup = groups.length > 1;
  const defaultDirsCollapsed =
    !staged && entries.length >= COLLAPSE_DIRS_THRESHOLD;
  /** 用户显式展开/折叠覆盖默认；未记录的目录走 defaultDirsCollapsed。 */
  const [dirCollapsedOverride, setDirCollapsedOverride] = useState<
    Record<string, boolean>
  >({});

  const isDirCollapsed = (dir: string) => {
    const key = dir || ".";
    if (key in dirCollapsedOverride) {
      return dirCollapsedOverride[key] === true;
    }
    return defaultDirsCollapsed;
  };

  const toggleDir = (dir: string) => {
    const key = dir || ".";
    setDirCollapsedOverride((prev) => ({
      ...prev,
      [key]: !isDirCollapsed(dir),
    }));
  };

  return (
    <>
      {groups.map((g) => {
        const showHeader = Boolean(g.dir) || multiGroup;
        const paths = g.entries.map((e) => e.path);
        const discardable = g.entries.filter((e) =>
          canDiscardChange(e, staged),
        );
        const untracked = g.entries.filter((e) => isUntrackedChange(e));
        const collapsed = showHeader && isDirCollapsed(g.dir);
        const fullDirTitle = g.dir || "仓根";

        return (
          <div key={`${keyPrefix}:${g.dir || "."}`}>
            {showHeader ? (
              <div className="group flex h-6 items-center gap-0.5 px-1.5 hover:bg-muted/40">
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center gap-0.5 text-left"
                  onClick={() => toggleDir(g.dir)}
                  aria-expanded={!collapsed}
                  title={fullDirTitle}
                >
                  {collapsed ? (
                    <ChevronRight
                      size={12}
                      className="shrink-0 text-muted-foreground"
                    />
                  ) : (
                    <ChevronDown
                      size={12}
                      className="shrink-0 text-muted-foreground"
                    />
                  )}
                  <span className="min-w-0 truncate text-xs text-muted-foreground/70">
                    {shortDirLabel(g.dir)}
                  </span>
                  <span className="shrink-0 tabular-nums text-xs text-muted-foreground/50">
                    {g.entries.length}
                  </span>
                </button>
                <span className={hoverOnlyActionsClass}>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-5 shrink-0 px-1"
                    onClick={() =>
                      void (
                        staged
                          ? gitUnstage(rootId, paths)
                          : gitStage(rootId, paths)
                      ).then((ok) => ok && onMutated())
                    }
                    aria-label={staged ? "取消暂存本组" : "暂存本组"}
                    title={staged ? "取消暂存本组" : "暂存本组"}
                  >
                    {staged ? <Minus size={11} /> : <Plus size={11} />}
                  </Button>
                  {!staged && discardable.length > 0 ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-5 shrink-0 px-1"
                      onClick={() =>
                        void gitDiscard(
                          rootId,
                          discardable.map((e) => e.path),
                        ).then((ok) => ok && onMutated())
                      }
                      aria-label="丢弃本组改动"
                      title="丢弃本组未暂存改动"
                    >
                      <Undo2 size={11} className="text-muted-foreground" />
                    </Button>
                  ) : null}
                  {!staged && untracked.length > 0 ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-5 shrink-0 px-1"
                      onClick={() => {
                        const rels = untracked
                          .map((e) => repoPathToWorkspaceRel(e.path, subpath))
                          .filter((p): p is string => p != null && p !== "");
                        void deleteUntrackedFiles(rootId, rels).then(
                          (ok) => ok && onMutated(),
                        );
                      }}
                      aria-label="删除本组未跟踪文件"
                      title="本组未跟踪文件移入回收站"
                    >
                      <Trash2 size={11} className="text-muted-foreground" />
                    </Button>
                  ) : null}
                </span>
              </div>
            ) : null}
            {!collapsed
              ? g.entries.map((e) => (
                  <ChangeRow
                    key={`${keyPrefix}:${e.path}:${e.code}`}
                    entry={e}
                    staged={staged}
                    rootId={rootId}
                    subpath={subpath}
                    onMutated={onMutated}
                    onOpenFile={onOpenFile}
                    hideDir={showHeader}
                  />
                ))
              : null}
          </div>
        );
      })}
    </>
  );
}

export function GitChangesSection({
  rootId,
  status,
  onRefresh,
  subpath = "",
}: {
  rootId: string;
  status: PresentGitRepoStatus;
  onRefresh: () => void;
  /** Workspace subpath under the container root; git paths stay repo-root relative. */
  subpath?: string;
}) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<"commit" | "push" | "pull" | "fetch" | null>(
    null,
  );
  /** 默认收起；有暂存时展开（挂载已有暂存 / 0→>0）。 */
  const [commitOpen, setCommitOpen] = useState(() => status.staged.length > 0);
  const [stagedOpen, setStagedOpen] = useState(true);
  const [unstagedOpen, setUnstagedOpen] = useState(false);
  const messageRef = useRef<HTMLTextAreaElement>(null);
  const prevHasStagedRef = useRef(status.staged.length > 0);
  const openFileTab = useSidePanelStore((s) => s.openFileTab);

  const openRepoFile = useCallback(
    (repoPath: string) => {
      const wsRel = repoPathToWorkspaceRel(repoPath, subpath);
      if (wsRel == null) {
        notifyInfo("该文件不在当前工作区内", { description: repoPath });
        return;
      }
      openFileTab(wsRel, basename(wsRel) || basename(repoPath));
    },
    [subpath, openFileTab],
  );

  const hasStaged = status.staged.length > 0;
  const hasUnstaged = status.unstaged.length > 0;
  const hasConflict = status.conflicted.length > 0;
  const discardableUnstaged = status.unstaged.filter((e) =>
    canDiscardChange(e, false),
  );
  const stagedCount = status.staged.length;

  useEffect(() => {
    const prev = prevHasStagedRef.current;
    if (!prev && hasStaged) setCommitOpen(true);
    else if (prev && !hasStaged && !message.trim()) setCommitOpen(false);
    prevHasStagedRef.current = hasStaged;
  }, [hasStaged, message]);

  useEffect(() => {
    if (commitOpen) messageRef.current?.focus();
  }, [commitOpen]);

  const onCommit = async () => {
    const msg = message.trim();
    if (!msg || !hasStaged) return;
    setBusy("commit");
    const ok = await gitCommit(rootId, msg);
    setBusy(null);
    if (ok) {
      setMessage("");
      setCommitOpen(false);
      onRefresh();
    }
  };

  const onPush = async () => {
    setBusy("push");
    const ok = await gitPush(rootId);
    setBusy(null);
    if (ok) onRefresh();
  };

  const onPull = async () => {
    setBusy("pull");
    const ok = await gitPull(rootId);
    setBusy(null);
    if (ok) onRefresh();
  };

  const onFetch = async () => {
    setBusy("fetch");
    const ok = await gitFetch(rootId);
    setBusy(null);
    if (ok) onRefresh();
  };

  return (
    <section
      className="border-t border-border/50"
      data-testid="git-changes-section"
    >
      <header className="flex items-center gap-1.5 border-b border-border/40 px-2 py-1">
        <GitBranch size={12} className="shrink-0 text-muted-foreground" />
        <h3 className="min-w-0 flex-1 truncate text-xs font-medium text-muted-foreground">
          Git · {status.branch}
          {status.ahead > 0 || status.behind > 0 ? (
            <span className="ml-1.5 tabular-nums text-muted-foreground/80">
              {status.ahead > 0 ? `↑${status.ahead}` : ""}
              {status.ahead > 0 && status.behind > 0 ? " " : ""}
              {status.behind > 0 ? `↓${status.behind}` : ""}
            </span>
          ) : null}
        </h3>
        <div className="flex shrink-0 gap-0">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="size-6 shrink-0 px-0"
            disabled={busy !== null}
            onClick={() => void onFetch()}
            aria-label="获取"
            title="获取远端（不合并）"
          >
            {busy === "fetch" ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <RefreshCw size={12} />
            )}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="size-6 shrink-0 px-0"
            disabled={busy !== null}
            onClick={() => void onPull()}
            aria-label="拉取"
            title="拉取并合并"
          >
            {busy === "pull" ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <ArrowDownToLine size={12} />
            )}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="size-6 shrink-0 px-0"
            disabled={busy !== null}
            onClick={() => void onPush()}
            aria-label="推送"
            title="推送到远端"
          >
            {busy === "push" ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <ArrowUpFromLine size={12} />
            )}
          </Button>
        </div>
      </header>

      {hasConflict ? (
        <output
          className="block border-b border-border/40 bg-warning/10 px-2 py-1.5 text-xs text-foreground"
          data-testid="git-conflict-banner"
        >
          <p className="font-medium">存在合并冲突</p>
          <p className="mt-0.5 text-muted-foreground">
            请打开文件手动解决后暂存提交（不做三方合并 UI）。
          </p>
          <ul className="mt-1 space-y-0.5">
            {status.conflicted.map((p) => (
              <li key={p}>
                <button
                  type="button"
                  className="text-left text-primary underline-offset-2 hover:underline"
                  onClick={() => openRepoFile(p)}
                >
                  {basename(p)}
                  {p.includes("/") ? (
                    <span className="ml-1 text-muted-foreground/70">
                      {shortDirLabel(
                        p.replace(/\\/g, "/").split("/").slice(0, -1).join("/"),
                      )}
                    </span>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>
        </output>
      ) : null}

      {/* Commit：仅有暂存时出现，避免空态全宽灰按钮占位 */}
      {hasStaged ? (
        <div className="border-b border-border/40 p-1.5">
          {commitOpen ? (
            <div className="overflow-hidden rounded-lg border border-border/50 bg-muted/20 focus-within:border-border">
              <Textarea
                ref={messageRef}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="提交说明"
                rows={2}
                className="min-h-0 resize-none rounded-none border-0 bg-transparent px-2 py-1.5 text-xs shadow-none focus-visible:ring-0"
                disabled={busy !== null}
                data-testid="git-commit-message"
              />
              <div className="flex items-center justify-between border-t border-border/30 px-1 py-0.5">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs text-muted-foreground"
                  disabled={busy !== null}
                  onClick={() => setCommitOpen(false)}
                  data-testid="git-commit-collapse"
                >
                  收起
                </Button>
                <Button
                  type="button"
                  size="sm"
                  className="h-6 px-2.5 text-xs"
                  disabled={!message.trim() || busy !== null}
                  onClick={() => void onCommit()}
                  data-testid="git-commit-button"
                >
                  {busy === "commit" ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    "提交"
                  )}
                </Button>
              </div>
            </div>
          ) : (
            <Button
              type="button"
              variant="neutral"
              size="sm"
              className="h-7 w-full border-border/50 text-xs"
              disabled={busy !== null}
              onClick={() => setCommitOpen(true)}
              data-testid="git-commit-open"
            >
              {`提交 · ${stagedCount}`}
            </Button>
          )}
        </div>
      ) : null}

      {hasStaged ? (
        <div className="border-b border-border/40">
          <div className="flex items-center gap-0.5 px-1 py-0.5">
            <button
              type="button"
              className="flex min-w-0 flex-1 items-center gap-1 rounded-lg px-1 py-0.5 text-left hover:bg-muted/50"
              onClick={() => setStagedOpen((v) => !v)}
              aria-expanded={stagedOpen}
            >
              {stagedOpen ? (
                <ChevronDown
                  size={12}
                  className="shrink-0 text-muted-foreground"
                />
              ) : (
                <ChevronRight
                  size={12}
                  className="shrink-0 text-muted-foreground"
                />
              )}
              <span className="text-xs text-muted-foreground">
                已暂存 · {status.staged.length}
              </span>
              <StatusSummary entries={status.staged} />
            </button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 shrink-0 px-1.5 text-xs"
              onClick={() =>
                void gitUnstage(rootId).then((ok) => ok && onRefresh())
              }
            >
              全部取消
            </Button>
          </div>
          {stagedOpen ? (
            <ChangeGroupList
              entries={status.staged}
              staged
              rootId={rootId}
              subpath={subpath}
              onMutated={onRefresh}
              onOpenFile={openRepoFile}
              keyPrefix="s"
            />
          ) : null}
        </div>
      ) : null}

      {hasUnstaged ? (
        <div>
          <div className="flex items-center gap-0.5 px-1 py-0.5">
            <button
              type="button"
              className="flex min-w-0 flex-1 items-center gap-1 rounded-lg px-1 py-0.5 text-left hover:bg-muted/50"
              onClick={() => setUnstagedOpen((v) => !v)}
              aria-expanded={unstagedOpen}
            >
              {unstagedOpen ? (
                <ChevronDown
                  size={12}
                  className="shrink-0 text-muted-foreground"
                />
              ) : (
                <ChevronRight
                  size={12}
                  className="shrink-0 text-muted-foreground"
                />
              )}
              <span className="text-xs text-muted-foreground">
                未暂存 · {status.unstaged.length}
              </span>
              <StatusSummary entries={status.unstaged} />
            </button>
            {discardableUnstaged.length > 0 ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 shrink-0 px-1 text-muted-foreground/70 hover:text-muted-foreground"
                onClick={() =>
                  void gitDiscard(
                    rootId,
                    discardableUnstaged.map((e) => e.path),
                  ).then((ok) => ok && onRefresh())
                }
                aria-label="全部丢弃"
                title="丢弃全部已跟踪的未暂存改动"
              >
                <Undo2 size={12} />
              </Button>
            ) : null}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 shrink-0 px-1.5 text-xs"
              onClick={() =>
                void gitStage(rootId).then((ok) => ok && onRefresh())
              }
            >
              全部暂存
            </Button>
          </div>
          {unstagedOpen ? (
            <ChangeGroupList
              entries={status.unstaged}
              staged={false}
              rootId={rootId}
              subpath={subpath}
              onMutated={onRefresh}
              onOpenFile={openRepoFile}
              keyPrefix="u"
            />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
