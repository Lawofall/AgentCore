import { type DiffLine, lineDiff } from "@/components/chat/toolResult/diff";
import { Button } from "@/components/ui";
import { useConversationWorkspace } from "@/hooks/useWorkspaces";
import type { FileArtifact, FileChangePreview } from "@/lib/fileArtifacts";
import { baseName } from "@/lib/fileSource";
import { notifyActionError, notifySuccess } from "@/lib/toast";
import {
  type TurnFileChange,
  getLocalTurnFilesDiff,
  getTurnFilesDiff,
  restoreLocalTurnBaseline,
} from "@/services/turnFilesDiff";
import { downloadWorkspaceFile, restoreSnapshot } from "@/services/workspace";
import { saveBlob } from "@/services/workspaceHttp";
import type { WorkspaceOpName } from "@shared/ipc-contract";
import {
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  RotateCcw,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";

/**
 * A1 / A1+ 只读「查看改动」——右坞「改动」tab。
 * 优先拉回合基线真 diff（A1+）；无基线 / 失败则降级工具参数预览（A1）。
 * 标签按路径相对回合初是否存在（新建/更新/删除），不按 file_write/str_replace 工具名。
 * 有 Local zip 基线即可恢复（不依赖 file_* 产物；P0c）。
 * 不做 apply / 三方冲突（与交接「查看改动并合回本机」刻意区分）。
 *
 * 信息架构：折叠头 = 唯一身份条（路径 + 变更态 + 行统计）；展开体 = 纯 diff / 预览，不再套路径标题。
 */

const WRITE_PREVIEW_LINES = 300;

/** 能力边界诚实文案（基线 = 回合开始 overlay；忽略目录不进包）。 */
const BASELINE_RESTORE_HINT =
  "尽最大努力回到本回合开始（覆盖当前工作区 overlay；未进基线的目录如 node_modules/.venv 等不会还原）";

/** 真 diff：按回合基线路径是否存在 → 新建/更新/删除（非工具名）。 */
function turnChangeLabel(changeType: TurnFileChange["changeType"]): string {
  if (changeType === "added") return "新建";
  if (changeType === "deleted") return "删除";
  return "更新";
}

/**
 * 无基线降级：工具参数预览无法判定「回合初是否存在」，
 * write/edit 一律标「更新」，勿用写入/编辑冒充用户语义。
 */
function previewKindLabel(change: FileChangePreview): string {
  if (change.kind === "delete") return "删除";
  if (change.kind === "move") return "移动";
  return "更新";
}

function writeModeLabel(mode: "overwrite" | "append" | "added"): string {
  if (mode === "append") return "追加";
  if (mode === "added") return "新建";
  return "更新";
}

function diffSign(type: DiffLine["type"]): string {
  if (type === "add") return "+";
  if (type === "del") return "-";
  return " ";
}

function diffRowClass(type: DiffLine["type"]): string {
  if (type === "add") return "bg-success/10 text-foreground";
  if (type === "del") return "bg-destructive/10 text-foreground";
  return "text-muted-foreground";
}

function summarizeLineDiff(
  oldText: string,
  newText: string,
): {
  lines: DiffLine[];
  adds: number;
  dels: number;
} {
  const lines = lineDiff(oldText, newText);
  let adds = 0;
  let dels = 0;
  for (const l of lines) {
    if (l.type === "add") adds += 1;
    else if (l.type === "del") dels += 1;
  }
  return { lines, adds, dels };
}

/** 展开体：纯行 diff，无路径标题（身份在折叠头）。 */
function DiffBody({ lines }: { lines: DiffLine[] }) {
  return (
    <div className="max-h-72 overflow-auto rounded-lg border border-border font-mono text-xs leading-relaxed">
      {lines.map((l, i) => (
        <div
          // biome-ignore lint/suspicious/noArrayIndexKey: stable positional diff rows
          key={i}
          className={`flex ${diffRowClass(l.type)}`}
        >
          <span className="w-5 shrink-0 select-none text-center text-muted-foreground/50">
            {diffSign(l.type)}
          </span>
          <span className="whitespace-pre-wrap break-words pr-2">
            {l.text || " "}
          </span>
        </div>
      ))}
    </div>
  );
}

/** 展开体：纯内容预览，无路径 / 模式标题。 */
function WriteBody({ content }: { content: string }) {
  const allLines = content.split("\n");
  const shown = allLines.slice(0, WRITE_PREVIEW_LINES);
  const hidden = allLines.length - shown.length;
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div className="max-h-72 overflow-auto font-mono text-xs leading-relaxed">
        {shown.map((line, i) => (
          <div
            // biome-ignore lint/suspicious/noArrayIndexKey: stable positional preview
            key={i}
            className="flex"
          >
            <span className="w-8 shrink-0 select-none pr-2 text-right text-muted-foreground/40">
              {i + 1}
            </span>
            <span className="whitespace-pre-wrap break-words pr-2 text-foreground/90">
              {line || " "}
            </span>
          </div>
        ))}
      </div>
      {hidden > 0 && (
        <div className="border-border/60 border-t bg-muted/40 px-2.5 py-1 text-muted-foreground text-xs">
          … 还有 {hidden} 行（共 {allLines.length} 行）
        </div>
      )}
    </div>
  );
}

/** 展开体：删除/移动说明，不重复路径。 */
function MetaBody({ detail }: { detail: string }) {
  return (
    <div className="rounded-lg border border-border bg-muted/30 px-2.5 py-2 font-mono text-xs text-muted-foreground">
      {detail}
    </div>
  );
}

function FileChangeChrome({
  path,
  open,
  onToggle,
  trailing,
}: {
  path: string;
  open: boolean;
  onToggle: () => void;
  trailing: ReactNode;
}) {
  return (
    <Button
      variant="ghost"
      onClick={onToggle}
      className="h-auto w-full justify-start gap-1.5 rounded-lg px-1.5 py-1 hover:bg-accent/50"
    >
      <span className="flex w-full items-center gap-1.5 text-left text-xs">
        {open ? (
          <ChevronDown size={12} className="shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight size={12} className="shrink-0 text-muted-foreground" />
        )}
        <span
          className="min-w-0 truncate font-mono text-foreground"
          title={path}
        >
          {path}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5 tabular-nums text-muted-foreground">
          {trailing}
        </span>
      </span>
    </Button>
  );
}

function EditTrailing({
  adds,
  dels,
  label,
}: { adds: number; dels: number; label: string }) {
  return (
    <>
      <span>{label}</span>
      <span className="text-success">+{adds}</span>
      <span className="text-destructive">-{dels}</span>
    </>
  );
}

function ArtifactChangeRow({ artifact }: { artifact: FileArtifact }) {
  const [open, setOpen] = useState(false);
  const change = artifact.change;
  const editDiff = useMemo(() => {
    if (!change || change.kind !== "edit") return null;
    return summarizeLineDiff(change.oldText, change.newText);
  }, [change]);

  if (!change) {
    return (
      <div className="px-1 py-1 text-xs text-muted-foreground">
        <span className="font-mono text-foreground">{artifact.path}</span>
        <span className="mx-1.5">·</span>
        无参数侧预览（可打开工作区查看终态）
      </div>
    );
  }

  const label = previewKindLabel(change);
  let trailing: ReactNode = label;
  if (change.kind === "edit" && editDiff) {
    trailing = (
      <EditTrailing adds={editDiff.adds} dels={editDiff.dels} label={label} />
    );
  } else if (change.kind === "write") {
    const lines = change.content.split("\n").length;
    trailing = (
      <>
        <span>{writeModeLabel(change.mode)}</span>
        <span>{lines} 行</span>
      </>
    );
  }

  return (
    <div className="space-y-1.5">
      <FileChangeChrome
        path={artifact.path}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        trailing={trailing}
      />
      {open && change.kind === "edit" && editDiff && (
        <DiffBody lines={editDiff.lines} />
      )}
      {open && change.kind === "write" && (
        <WriteBody content={change.content} />
      )}
      {open && change.kind === "delete" && <MetaBody detail="已删除" />}
      {open && change.kind === "move" && (
        <MetaBody
          detail={`移动：${change.fromPath || "?"} → ${artifact.path}`}
        />
      )}
    </div>
  );
}

/** Download a local-workspace file via sidecar `read_bytes` → save dialog. */
async function downloadLocalWorkspaceFile(
  rootId: string,
  subpath: string,
  relPath: string,
  filename: string,
): Promise<void> {
  const fsApi = window.fsApi;
  if (!fsApi?.workspaceOp) {
    throw new Error("本机文件接口不可用");
  }
  const base = subpath.replace(/^\/+|\/+$/g, "");
  const path = base ? `${base}/${relPath}` : relPath;
  const res = await fsApi.workspaceOp(rootId, "read_bytes" as WorkspaceOpName, {
    path,
  });
  if (!res.ok || typeof res.value !== "string") {
    throw new Error(
      !res.ok ? res.error.detail || res.error.kind : "读取本机文件失败",
    );
  }
  const bin = atob(res.value);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  await saveBlob(new Blob([bytes]), filename);
}

function BinaryChangeBody({
  change,
  conversationId,
  isLocal,
  localRootId,
  localSubpath,
}: {
  change: TurnFileChange;
  conversationId: string | null;
  isLocal: boolean;
  localRootId: string | null;
  localSubpath: string;
}) {
  const [downloading, setDownloading] = useState(false);
  const filename = baseName(change.path) || change.path;
  const canDownload =
    change.changeType !== "deleted" &&
    ((isLocal && !!localRootId) || (!isLocal && !!conversationId));

  const onDownload = async () => {
    if (!canDownload || downloading) return;
    setDownloading(true);
    try {
      if (isLocal && localRootId) {
        await downloadLocalWorkspaceFile(
          localRootId,
          localSubpath,
          change.path,
          filename,
        );
      } else if (conversationId) {
        await downloadWorkspaceFile(conversationId, change.path, filename);
      }
      notifySuccess(`已下载 ${filename}`);
    } catch (e) {
      notifyActionError("下载失败", e);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="space-y-2 rounded-lg border border-border bg-muted/30 px-2.5 py-2 text-xs text-muted-foreground">
      <p>
        二进制文件（{change.sizeBytes} 字节）
        {canDownload ? "— 可直接下载到本机" : "— 暂无法下载（工作区未就绪）"}
      </p>
      {canDownload && (
        <Button
          variant="neutral"
          size="sm"
          disabled={downloading}
          onClick={() => void onDownload()}
          aria-label={`下载 ${filename}`}
          className="h-7 gap-1.5 px-2 text-xs"
        >
          {downloading ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <Download size={13} />
          )}
          下载
        </Button>
      )}
    </div>
  );
}

function TrueDiffRow({
  change,
  conversationId,
  isLocal,
  localRootId,
  localSubpath,
}: {
  change: TurnFileChange;
  conversationId: string | null;
  isLocal: boolean;
  localRootId: string | null;
  localSubpath: string;
}) {
  const [open, setOpen] = useState(false);
  const editDiff = useMemo(() => {
    if (
      change.changeType !== "modified" ||
      change.isBinary ||
      change.baseContent == null ||
      change.content == null
    ) {
      return null;
    }
    return summarizeLineDiff(change.baseContent, change.content);
  }, [change]);

  const label = turnChangeLabel(change.changeType);
  let trailing: ReactNode = label;
  if (editDiff) {
    trailing = (
      <EditTrailing adds={editDiff.adds} dels={editDiff.dels} label={label} />
    );
  } else if (
    change.changeType === "added" &&
    !change.isBinary &&
    change.content != null
  ) {
    const lines = change.content.split("\n").length;
    trailing = (
      <>
        <span>{label}</span>
        <span>{lines} 行</span>
      </>
    );
  }

  return (
    <div className="space-y-1.5">
      <FileChangeChrome
        path={change.path}
        open={open}
        onToggle={() => setOpen((v) => !v)}
        trailing={trailing}
      />
      {open && editDiff && <DiffBody lines={editDiff.lines} />}
      {open &&
        change.changeType === "added" &&
        !change.isBinary &&
        change.content != null && <WriteBody content={change.content} />}
      {open && change.changeType === "deleted" && <MetaBody detail="已删除" />}
      {open && change.isBinary && change.changeType !== "deleted" && (
        <BinaryChangeBody
          change={change}
          conversationId={conversationId}
          isLocal={isLocal}
          localRootId={localRootId}
          localSubpath={localSubpath}
        />
      )}
    </div>
  );
}

function ToolArgFallback({ artifacts }: { artifacts: FileArtifact[] }) {
  return (
    <>
      {artifacts.map((a) => (
        <ArtifactChangeRow key={`${a.op}:${a.path}`} artifact={a} />
      ))}
    </>
  );
}

function ChangeCounts({
  counts,
}: {
  counts: { added: number; modified: number; deleted: number };
}) {
  return (
    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
      <span className="text-success">+{counts.added}</span>
      <span className="mx-1 text-primary">~{counts.modified}</span>
      <span className="text-destructive">-{counts.deleted}</span>
    </span>
  );
}

function RestoreTurnButton({
  restoring,
  onRestore,
}: {
  restoring: boolean;
  onRestore: () => void;
}) {
  return (
    <Button
      variant="ghost"
      size="sm"
      disabled={restoring}
      onClick={onRestore}
      aria-label="恢复到本回合开始"
      title={BASELINE_RESTORE_HINT}
      className="h-7 shrink-0 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
    >
      {restoring ? (
        <Loader2 size={13} className="animate-spin" />
      ) : (
        <RotateCcw size={13} />
      )}
      恢复
    </Button>
  );
}

function TurnChrome({
  isPanel,
  divided,
  heading,
  headingTime,
  loading,
  counts,
  restore,
}: {
  isPanel: boolean;
  divided: boolean;
  heading?: string;
  headingTime?: string;
  loading: boolean;
  counts: { added: number; modified: number; deleted: number } | null;
  restore: ReactNode;
}) {
  return (
    <header
      className={
        isPanel
          ? `flex items-center gap-2 px-3 py-2${divided ? " border-b border-border" : ""}`
          : "flex flex-wrap items-center gap-x-3 gap-y-1"
      }
    >
      {heading ? (
        <h3 className="shrink-0 text-xs font-medium text-muted-foreground">
          {heading}
        </h3>
      ) : null}
      {loading ? (
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 size={13} className="animate-spin" />
          {heading ? null : "正在读取改动…"}
        </span>
      ) : null}
      {!loading && counts ? <ChangeCounts counts={counts} /> : null}
      <div className="min-w-0 flex-1" />
      {restore}
      {headingTime ? (
        <span className="shrink-0 text-xs text-muted-foreground">
          {headingTime}
        </span>
      ) : null}
    </header>
  );
}

export function TurnFileChangesReview({
  artifacts,
  conversationId = null,
  messageId = null,
  variant = "card",
  heading,
  headingTime,
}: {
  artifacts: FileArtifact[];
  conversationId?: string | null;
  /** Assistant message id（= turnKey）；有则尝试 A1+ 真 diff。 */
  messageId?: string | null;
  /** `card` 历史别名；`panel` = 右坞「改动」tab，与 heading / 时间同一行壳。 */
  variant?: "card" | "panel";
  heading?: string;
  headingTime?: string;
}) {
  const ws = useConversationWorkspace(conversationId);
  const isLocal = ws?.location === "local" && !!ws.rootId;
  const [phase, setPhase] = useState<"loading" | "true" | "fallback">(
    conversationId && messageId ? "loading" : "fallback",
  );
  const [trueChanges, setTrueChanges] = useState<TurnFileChange[] | null>(null);
  const [baselineSnapshotId, setBaselineSnapshotId] = useState<string | null>(
    null,
  );
  const [counts, setCounts] = useState<{
    added: number;
    modified: number;
    deleted: number;
  } | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadToken is an intentional re-run key after rollback
  useEffect(() => {
    if (!conversationId || !messageId) {
      setPhase("fallback");
      return;
    }
    let cancelled = false;
    setPhase("loading");
    const load = isLocal
      ? getLocalTurnFilesDiff(
          { rootId: ws.rootId as string, subpath: ws.subpath ?? "" },
          messageId,
        )
      : getTurnFilesDiff(conversationId, messageId);
    void load
      .then((diff) => {
        if (cancelled) return;
        if (diff.available) {
          setTrueChanges(diff.changes);
          setBaselineSnapshotId(diff.baselineSnapshotId);
          setCounts({
            added: diff.added,
            modified: diff.modified,
            deleted: diff.deleted,
          });
          setPhase("true");
        } else {
          setBaselineSnapshotId(null);
          setPhase("fallback");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBaselineSnapshotId(null);
          setPhase("fallback");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [
    conversationId,
    messageId,
    reloadToken,
    isLocal,
    ws?.rootId,
    ws?.subpath,
  ]);

  const onRollback = async () => {
    if (!conversationId || !baselineSnapshotId || restoring) return;
    const where = isLocal ? "本机工作区" : "工作区";
    const confirmMsg = `${BASELINE_RESTORE_HINT}。将覆盖当前${where}内基线所含文件，确定继续？`;
    if (!window.confirm(confirmMsg)) {
      return;
    }
    setRestoring(true);
    try {
      if (isLocal && ws?.rootId) {
        await restoreLocalTurnBaseline(
          { rootId: ws.rootId, subpath: ws.subpath ?? "" },
          baselineSnapshotId,
        );
      } else {
        await restoreSnapshot(conversationId, baselineSnapshotId);
      }
      setReloadToken((n) => n + 1);
    } catch (e) {
      notifyActionError("恢复失败", e);
    } finally {
      setRestoring(false);
    }
  };

  const isPanel = variant === "panel";
  const emptyFallback =
    artifacts.length === 0 && phase !== "true" && phase !== "loading";
  if (emptyFallback && !isPanel) {
    return null;
  }

  const showRestore =
    phase === "true" && !!baselineSnapshotId && !!conversationId;
  const showCounts = phase === "true" && counts != null;
  const emptyTrueDiff =
    phase === "true" && trueChanges != null && trueChanges.length === 0;
  const fileRows =
    phase === "true" && trueChanges && trueChanges.length > 0 ? (
      trueChanges.map((c) => (
        <TrueDiffRow
          key={`${c.changeType}:${c.path}`}
          change={c}
          conversationId={conversationId}
          isLocal={isLocal}
          localRootId={isLocal ? (ws?.rootId ?? null) : null}
          localSubpath={ws?.subpath ?? ""}
        />
      ))
    ) : phase === "fallback" && artifacts.length > 0 ? (
      <ToolArgFallback artifacts={artifacts} />
    ) : null;
  const body =
    fileRows ??
    (emptyTrueDiff ? (
      <p className="text-xs text-muted-foreground">相对基线无文件差异</p>
    ) : isPanel && emptyFallback ? (
      <p className="text-xs text-muted-foreground">暂无改动</p>
    ) : null);
  const showChrome =
    isPanel || phase === "loading" || showCounts || showRestore;

  return (
    <div
      className={
        isPanel
          ? undefined
          : "space-y-3 border-t border-border bg-muted/20 px-3 py-2.5"
      }
    >
      {showChrome ? (
        <TurnChrome
          isPanel={isPanel}
          divided={isPanel && body != null}
          heading={heading}
          headingTime={headingTime}
          loading={phase === "loading"}
          counts={showCounts ? counts : null}
          restore={
            showRestore ? (
              <RestoreTurnButton
                restoring={restoring}
                onRestore={() => void onRollback()}
              />
            ) : null
          }
        />
      ) : null}
      {body == null ? null : isPanel ? (
        <div className="space-y-3 px-3 py-2.5">{body}</div>
      ) : (
        body
      )}
    </div>
  );
}
