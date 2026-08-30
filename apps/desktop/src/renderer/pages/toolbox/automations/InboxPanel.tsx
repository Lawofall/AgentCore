import { Button, Card, EmptyHint } from "@/components/ui";
import { notifyError, notifySuccess } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api";
import {
  type StandingTaskRun,
  type StandingTaskRunStatus,
  ackStandingTaskRun,
  listStandingTaskRuns,
  runStandingTaskNow,
  triggerSourceLabel,
} from "@/services/standingTasks";
import { useStandingInboxStore } from "@/stores/standingInbox";
import {
  AlertTriangle,
  CheckCircle2,
  Hand,
  Loader2,
  MessageSquare,
  RotateCcw,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof ApiError ? (e.serverMessage ?? fallback) : fallback;
}

function formatWhen(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/**
 * 收件箱筛选。是「筛过一遍同一份列表」而非换页，所以做成圆角 chip +
 * `aria-pressed`，与二级下划线 tab（导航）形态分开。
 */
const FILTERS = [
  { id: "all", label: "全部" },
  { id: "actionable", label: "待处理" },
] as const;

type InboxFilter = (typeof FILTERS)[number]["id"];

const STATUS_META: Record<
  StandingTaskRunStatus,
  { label: string; className: string; Icon: typeof CheckCircle2 }
> = {
  succeeded: {
    label: "已完成",
    className: "bg-success/10 text-success",
    Icon: CheckCircle2,
  },
  failed: {
    label: "失败",
    className: "bg-destructive/10 text-destructive",
    Icon: AlertTriangle,
  },
  awaiting_user: {
    label: "待拍板",
    className: "bg-warning/10 text-warning",
    Icon: Hand,
  },
  running: {
    label: "进行中",
    className: "bg-muted text-muted-foreground",
    Icon: Loader2,
  },
};

/**
 * 自动化 · 收件箱。复用原设置页能力与数据，换壳进专页。
 */
export function InboxPanel() {
  const navigate = useNavigate();
  const refreshBadge = useStandingInboxStore((s) => s.refresh);
  const [runs, setRuns] = useState<StandingTaskRun[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [filter, setFilter] = useState<InboxFilter>("all");

  const load = useCallback(async () => {
    setListError(null);
    try {
      const list = await listStandingTaskRuns({ limit: 50 });
      setRuns(list);
      void refreshBadge();
    } catch (e) {
      setListError(errMsg(e, "加载收件箱失败（后端可能尚未就绪）"));
      setRuns([]);
    }
  }, [refreshBadge]);

  useEffect(() => {
    void load();
  }, [load]);

  const visible = (runs ?? []).filter((r) => {
    if (filter === "all") return r.status !== "running";
    return (
      (r.status === "awaiting_user" && !r.ackedAt) ||
      (r.status === "failed" && !r.ackedAt)
    );
  });

  const openConversation = (run: StandingTaskRun) => {
    if (!run.conversationId) {
      notifyError("尚无关联对话");
      return;
    }
    navigate(`/conversations/${run.conversationId}`);
  };

  const onAck = async (run: StandingTaskRun) => {
    setBusyId(run.id);
    try {
      const next = await ackStandingTaskRun(run.id);
      setRuns((prev) => (prev ?? []).map((r) => (r.id === run.id ? next : r)));
      void refreshBadge();
    } catch (e) {
      notifyError(e, "操作失败");
    } finally {
      setBusyId(null);
    }
  };

  const onRerun = async (run: StandingTaskRun) => {
    setBusyId(run.id);
    try {
      const { runId } = await runStandingTaskNow(run.standingTaskId);
      if (!run.ackedAt) {
        try {
          await ackStandingTaskRun(run.id);
        } catch {
          /* ack is best-effort after rerun */
        }
      }
      notifySuccess(`已重新触发（${runId.slice(0, 8)}…）`);
      await load();
    } catch (e) {
      notifyError(e, "重新触发失败");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <p className="text-sm text-muted-foreground">
        自动化任务的运行结果：成功摘要、失败与待你拍板的挂起项。
      </p>

      <fieldset className="mt-4 flex w-fit items-center gap-1.5">
        <legend className="sr-only">收件箱筛选</legend>
        {FILTERS.map((f) => (
          <Button
            key={f.id}
            variant="ghost"
            size="sm"
            aria-pressed={filter === f.id}
            className={cn(
              "rounded-full",
              filter === f.id
                ? "bg-primary/15 text-foreground hover:bg-primary/15"
                : "text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </Button>
        ))}
      </fieldset>

      <section className="mt-6">
        {runs === null ? (
          <Loader2
            size={16}
            className="animate-spin text-muted-foreground/50"
          />
        ) : listError ? (
          <p className="text-sm text-muted-foreground">{listError}</p>
        ) : visible.length === 0 ? (
          <EmptyHint
            className="py-10"
            title={
              filter === "actionable" ? "没有待拍板或未读失败" : "收件箱是空的"
            }
            hint={
              filter === "actionable" ? undefined : "任务跑完后会出现在这里。"
            }
          />
        ) : (
          <ul className="space-y-3">
            {visible.map((run) => {
              const meta = STATUS_META[run.status];
              const Icon = meta.Icon;
              const busy = busyId === run.id;
              return (
                <li key={run.id}>
                  <Card
                    className={cn(
                      "px-4 py-3",
                      run.status === "failed" &&
                        !run.ackedAt &&
                        "border-destructive/30",
                      run.status === "awaiting_user" && "border-warning/30",
                    )}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={cn(
                          "inline-flex items-center gap-1 rounded-lg px-2 py-0.5 text-xs font-medium",
                          meta.className,
                        )}
                      >
                        <Icon
                          size={12}
                          className={
                            run.status === "running"
                              ? "animate-spin"
                              : undefined
                          }
                        />
                        {meta.label}
                      </span>
                      {run.taskName && (
                        <span className="text-xs text-muted-foreground">
                          {run.taskName}
                        </span>
                      )}
                      {run.triggerSource && (
                        <span className="rounded-lg bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                          {triggerSourceLabel(run.triggerSource)}
                        </span>
                      )}
                      <span className="ml-auto text-xs text-muted-foreground">
                        {formatWhen(run.finishedAt ?? run.createdAt)}
                      </span>
                    </div>

                    {run.status === "succeeded" && (
                      <p className="mt-2 text-sm text-foreground">
                        {run.summary?.trim() || "本轮已完成，暂无摘要。"}
                      </p>
                    )}
                    {run.status === "failed" && (
                      <p className="mt-2 text-sm text-destructive">
                        {run.error?.trim() || "运行失败，未返回错误详情。"}
                      </p>
                    )}
                    {run.status === "awaiting_user" && (
                      <p className="mt-2 text-sm text-foreground">
                        {run.summary?.trim() ||
                          "需要你在对话里授权或拍板后才能继续。"}
                      </p>
                    )}

                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {run.conversationId && (
                        <Button
                          variant="neutral"
                          size="sm"
                          icon={<MessageSquare size={14} />}
                          onClick={() => openConversation(run)}
                        >
                          {run.status === "awaiting_user" ? "去拍板" : "进对话"}
                        </Button>
                      )}
                      {run.status === "failed" && (
                        <>
                          <Button
                            size="sm"
                            disabled={busy}
                            icon={
                              busy ? (
                                <Loader2 size={14} className="animate-spin" />
                              ) : (
                                <RotateCcw size={14} />
                              )
                            }
                            onClick={() => void onRerun(run)}
                          >
                            重新触发
                          </Button>
                          {!run.ackedAt && (
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={busy}
                              onClick={() => void onAck(run)}
                            >
                              关闭
                            </Button>
                          )}
                        </>
                      )}
                      {run.status === "awaiting_user" && !run.ackedAt && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={busy}
                          onClick={() => void onAck(run)}
                        >
                          关闭
                        </Button>
                      )}
                      {run.status === "succeeded" && !run.ackedAt && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={busy}
                          onClick={() => void onAck(run)}
                        >
                          标为已读
                        </Button>
                      )}
                    </div>
                  </Card>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
