import { Button, IconButton } from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { type ReviewRow, countChanges } from "@/lib/handoff-review";
import { applyMergeLandingDiff } from "@/services/mergeLandingDiff";
import {
  type MergeLandingReviewSession,
  useMergeLandingReviewStore,
} from "@/stores/mergeLandingReview";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CloudUpload,
  Loader2,
} from "lucide-react";
import { useCallback, useState } from "react";

/**
 * AppShell host：云桌「合回到本机」Diff 勾选（不绑 handoff job）。
 */
export function MergeLandingReviewHost() {
  const session = useMergeLandingReviewStore((s) => s.session);
  const resolveCancelled = useMergeLandingReviewStore(
    (s) => s.resolveCancelled,
  );
  const resolveApplied = useMergeLandingReviewStore((s) => s.resolveApplied);

  return (
    <MergeLandingReviewDialog
      session={session}
      onOpenChange={(open) => {
        if (!open) resolveCancelled();
      }}
      onApplied={resolveApplied}
      onDismiss={resolveCancelled}
    />
  );
}

export function MergeLandingReviewDialog({
  session,
  onOpenChange,
  onApplied,
  onDismiss,
}: {
  session: MergeLandingReviewSession | null;
  onOpenChange: (open: boolean) => void;
  onApplied: (summaryLabel: string) => void;
  onDismiss: () => void;
}) {
  return (
    <Dialog
      open={!!session}
      onOpenChange={(open) => {
        // 仅用户关窗时取消；apply 成功已由 resolveApplied 收口，避免二次 resolve。
        if (!open && session) onOpenChange(false);
      }}
    >
      <DialogContent size="xl" className="flex max-h-[85vh] flex-col">
        <DialogHeader>
          <DialogTitle>合回到本机</DialogTitle>
          <DialogDescription>
            {session
              ? `对照云端与落点「${session.rootName}」。冲突默认保留本机；不会静默覆盖。`
              : ""}
          </DialogDescription>
        </DialogHeader>
        {session ? (
          <MergeLandingReview
            session={session}
            onApplied={onApplied}
            onDismiss={onDismiss}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function MergeLandingReview({
  session,
  onApplied,
  onDismiss,
}: {
  session: MergeLandingReviewSession;
  onApplied: (summaryLabel: string) => void;
  onDismiss: () => void;
}) {
  const [rows, setRows] = useState<ReviewRow[]>(session.rows);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const setDecision = useCallback(
    (path: string, decision: ReviewRow["decision"]) => {
      setRows((prev) =>
        prev.map((r) => (r.change.path === path ? { ...r, decision } : r)),
      );
    },
    [],
  );

  const acceptAllClean = useCallback(() => {
    setRows((prev) =>
      prev.map((r) =>
        r.verdict === "conflict" ? r : { ...r, decision: "cloud" as const },
      ),
    );
  }, []);

  const toggleExpand = useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const onApply = useCallback(async () => {
    if (applying) return;
    setApplying(true);
    setApplyError(null);
    try {
      const summary = await applyMergeLandingDiff(
        session.rootId,
        rows,
        session.bytesByPath,
      );
      const parts: string[] = [];
      if (summary.applied) parts.push(`${summary.applied} 已写入`);
      if (summary.skipped) parts.push(`${summary.skipped} 跳过`);
      if (summary.conflicts) parts.push(`${summary.conflicts} 冲突未覆盖`);
      if (summary.errors) parts.push(`${summary.errors} 失败`);
      onApplied(parts.length ? parts.join(" · ") : "无变更");
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : "合回失败");
    } finally {
      setApplying(false);
    }
  }, [applying, rows, session, onApplied]);

  const actionable = rows.filter((r) => r.verdict !== "applied");
  const conflicts = rows.filter((r) => r.verdict === "conflict");
  const forced = conflicts.filter((r) => r.decision === "cloud").length;
  const counts = countChanges(rows.map((r) => r.change));
  const notices: string[] = [];
  if (session.skippedOversized.length) {
    notices.push(`${session.skippedOversized.length} 个文件超过 5MB，已跳过`);
  }
  if (session.skippedUnreadable.length) {
    notices.push(
      `${session.skippedUnreadable.length} 个落点文件过大或不可读，已跳过（防误覆盖）`,
    );
  }
  if (session.truncated) {
    notices.push("云端文件过多，已截断扫描");
  }

  if (actionable.length === 0) {
    return (
      <>
        <DialogBody className="space-y-3">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <CheckCircle2 size={14} className="text-success" />
            {notices.length > 0
              ? "没有可合入的文件（见下方提示）。"
              : "云端与落点一致，无需合回。"}
          </div>
          {notices.length > 0 && (
            <ul className="space-y-1 text-xs text-muted-foreground">
              {notices.map((n) => (
                <li key={n} className="flex items-start gap-1">
                  <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                  {n}
                </li>
              ))}
            </ul>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onDismiss}>
            关闭
          </Button>
        </DialogFooter>
      </>
    );
  }

  return (
    <>
      <DialogBody className="flex min-h-0 flex-1 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span className="text-success">+{counts.added}</span>
          <span className="text-primary">~{counts.modified}</span>
          {conflicts.length > 0 ? (
            <span className="flex items-center gap-1 text-primary">
              <AlertTriangle size={12} />
              {conflicts.length} 个冲突（默认保留本机）
            </span>
          ) : (
            <span className="text-muted-foreground">无冲突，可全部合入</span>
          )}
        </div>

        {notices.length > 0 && (
          <ul className="space-y-1 text-xs text-muted-foreground">
            {notices.map((n) => (
              <li key={n} className="flex items-start gap-1">
                <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                {n}
              </li>
            ))}
          </ul>
        )}

        {conflicts.length > 0 && (
          <ul className="max-h-56 space-y-1 overflow-auto">
            {conflicts.map((row) => {
              const canPreview =
                !row.change.isBinary && row.change.content !== null;
              const open = expanded.has(row.change.path);
              return (
                <li
                  key={row.change.path}
                  className="rounded-lg border border-primary/30 bg-primary/10"
                >
                  <div className="flex items-center gap-1.5 px-2 py-1.5">
                    <IconButton
                      onClick={() => toggleExpand(row.change.path)}
                      disabled={!canPreview}
                      aria-label="展开预览"
                      className="size-5 rounded disabled:opacity-30"
                    >
                      {open ? (
                        <ChevronDown size={13} />
                      ) : (
                        <ChevronRight size={13} />
                      )}
                    </IconButton>
                    <span
                      className="min-w-0 flex-1 truncate text-xs"
                      title={row.change.path}
                    >
                      {row.change.path}
                    </span>
                    <DecisionToggle
                      active={row.decision === "cloud"}
                      danger
                      onClick={() => setDecision(row.change.path, "cloud")}
                      label="用云端"
                    />
                    <DecisionToggle
                      active={row.decision === "local"}
                      onClick={() => setDecision(row.change.path, "local")}
                      label="保留本机"
                    />
                  </div>
                  {open && canPreview && (
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words border-t border-primary/30 px-2.5 py-2 font-mono text-xs leading-relaxed text-foreground">
                      {row.change.content}
                    </pre>
                  )}
                </li>
              );
            })}
          </ul>
        )}

        {forced > 0 && (
          <p className="flex items-center gap-1 text-xs text-destructive">
            <AlertTriangle size={12} />
            将强制覆盖 {forced} 个有本机改动的文件
          </p>
        )}
        {applyError && (
          <p className="text-xs text-muted-foreground">{applyError}</p>
        )}
      </DialogBody>

      <DialogFooter className="gap-2 sm:justify-between">
        <Button
          variant="neutral"
          className="border border-border"
          disabled={applying || conflicts.length === 0}
          onClick={acceptAllClean}
        >
          冲突保持本机 · 其余取云端
        </Button>
        <div className="flex gap-2">
          <Button variant="outline" onClick={onDismiss}>
            取消
          </Button>
          <Button
            icon={
              applying ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <CloudUpload size={13} />
              )
            }
            disabled={applying || actionable.length === 0}
            onClick={() => void onApply()}
          >
            {conflicts.length === 0
              ? `全部合入（${actionable.length}）`
              : "合入所选"}
          </Button>
        </div>
      </DialogFooter>
    </>
  );
}

function DecisionToggle({
  active,
  onClick,
  label,
  danger,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  danger?: boolean;
}) {
  const cls = active
    ? danger
      ? "border-destructive bg-destructive/10 text-destructive"
      : "border-primary bg-primary/10 text-primary"
    : "border-transparent text-muted-foreground hover:bg-accent";
  return (
    <Button
      variant="ghost"
      onClick={onClick}
      className={`h-auto shrink-0 rounded-lg border px-2 py-0.5 text-xs font-medium ${cls}`}
    >
      {label}
    </Button>
  );
}
