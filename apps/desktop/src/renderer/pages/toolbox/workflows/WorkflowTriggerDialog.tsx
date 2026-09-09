import { Button, Input } from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { copyText } from "@/lib/clipboard";
import { notifyError, notifySuccess } from "@/lib/toast";
import { ApiError } from "@/services/api";
import { type FolderMeta, listFolders } from "@/services/folders";
import {
  SCHEDULE_PRESET_LABELS,
  SCHEDULE_PRESET_ORDER,
  type SchedulePreset,
  TRIGGER_KIND_LABELS,
  TRIGGER_KIND_ORDER,
  type TriggerKind,
  type UserWorkflow,
  type WorkflowTrigger,
  deleteWorkflowTrigger,
  putWorkflowTrigger,
  rotateWorkflowTriggerSecret,
} from "@/services/workflows";
import { Check, Copy, KeyRound, Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const SELECT_CLASS =
  "h-8 w-full rounded-lg border border-border bg-background px-2.5 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-ring";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof ApiError ? (e.serverMessage ?? fallback) : fallback;
}

type FoldersState =
  | { status: "loading" }
  | { status: "ready"; items: FolderMeta[] }
  | { status: "error"; message: string };

interface FormState {
  kind: TriggerKind;
  schedulePreset: SchedulePreset;
  cron: string;
  folderId: string;
  enabled: boolean;
  webhookUrl: string | null;
  webhookId: string | null;
  revealedSecret: string | null;
}

function formFromTrigger(
  trigger: WorkflowTrigger | null | undefined,
  folders: FolderMeta[],
): FormState {
  const fallbackFolder = folders[0]?.id ?? "";
  if (!trigger) {
    return {
      kind: "schedule",
      schedulePreset: "weekly_mon",
      cron: "",
      folderId: fallbackFolder,
      enabled: true,
      webhookUrl: null,
      webhookId: null,
      revealedSecret: null,
    };
  }
  return {
    kind: trigger.kind,
    schedulePreset: trigger.schedulePreset ?? "weekly_mon",
    cron: trigger.cron ?? "",
    folderId: trigger.folderId || fallbackFolder,
    enabled: trigger.enabled,
    webhookUrl: trigger.webhookUrl,
    webhookId: trigger.webhookId,
    revealedSecret: trigger.webhookSecret,
  };
}

function applyKind(form: FormState, kind: TriggerKind): FormState {
  if (kind === form.kind) return form;
  if (kind === "webhook") {
    return {
      ...form,
      kind: "webhook",
      schedulePreset: "weekly_mon",
      cron: "",
    };
  }
  return {
    ...form,
    kind: "schedule",
    webhookUrl: null,
    webhookId: null,
    revealedSecret: null,
  };
}

async function copyField(label: string, value: string) {
  const ok = await copyText(value);
  if (ok) notifySuccess(`已复制${label}`);
  else notifyError(`复制${label}失败`);
}

/**
 * 「设为定时」本页对话框：定时 | Webhook 互斥，云文件夹、启用、preset/cron；
 * Webhook 相对 URL / 复制 / 轮换密钥。没有权限三轴，也没有「立即跑」。
 */
export function WorkflowTriggerDialog({
  open,
  workflow,
  onSaved,
  onClose,
}: {
  open: boolean;
  workflow: UserWorkflow;
  onSaved: (next: UserWorkflow) => void;
  onClose: () => void;
}) {
  const [foldersState, setFoldersState] = useState<FoldersState>({
    status: "loading",
  });
  const [form, setForm] = useState<FormState>(() =>
    formFromTrigger(workflow.trigger, []),
  );
  const [submitting, setSubmitting] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingDismiss, setPendingDismiss] = useState(false);
  const loadSeq = useRef(0);
  const hasTrigger = Boolean(workflow.trigger);

  const loadFolders = useCallback(async () => {
    const seq = ++loadSeq.current;
    setFoldersState({ status: "loading" });
    try {
      const all = await listFolders();
      if (seq !== loadSeq.current) return;
      const items = all.filter((f) => f.mode === "cloud");
      setFoldersState({ status: "ready", items });
      setForm((f) => ({
        ...f,
        folderId:
          f.folderId && items.some((x) => x.id === f.folderId)
            ? f.folderId
            : (items[0]?.id ?? ""),
      }));
    } catch (e) {
      if (seq !== loadSeq.current) return;
      setFoldersState({
        status: "error",
        message: errMsg(e, "文件夹列表加载失败"),
      });
    }
  }, []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: 只在打开或换工作流时回填，不跟 trigger 字段
  useEffect(() => {
    if (!open) return;
    setError(null);
    setPendingDismiss(false);
    setForm(formFromTrigger(workflow.trigger, []));
    void loadFolders();
  }, [open, workflow.id, loadFolders]);

  const ready = foldersState.status === "ready" ? foldersState : null;
  const noCloud = ready !== null && ready.items.length === 0;
  const foldersError =
    foldersState.status === "error" ? foldersState.message : null;

  const canSubmit = useMemo(() => {
    if (!form.folderId) return false;
    if (
      form.kind === "schedule" &&
      form.schedulePreset === "custom" &&
      !form.cron.trim()
    ) {
      return false;
    }
    return true;
  }, [form]);

  const requestClose = () => {
    if (pendingDismiss) {
      setPendingDismiss(false);
      onClose();
      return;
    }
    onClose();
  };

  const submit = async () => {
    if (!canSubmit || submitting) return;
    if (noCloud) {
      setError(
        foldersError
          ? `读不到文件夹列表（${foldersError}），暂时无法确认可用的云端文件夹，请重试后再保存。`
          : "请先在「我的文件」里新建一个文件夹（本机文件夹无法在关机时代跑）",
      );
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const saved = await putWorkflowTrigger(workflow.id, {
        kind: form.kind,
        folderId: form.folderId,
        enabled: form.enabled,
        schedulePreset:
          form.kind === "schedule" ? form.schedulePreset : undefined,
        cron:
          form.kind === "schedule" && form.schedulePreset === "custom"
            ? form.cron.trim() || null
            : undefined,
      });
      onSaved(saved);
      if (saved.trigger?.kind === "webhook" && saved.trigger.webhookSecret) {
        setForm((f) => ({
          ...f,
          webhookUrl: saved.trigger?.webhookUrl ?? f.webhookUrl,
          webhookId: saved.trigger?.webhookId ?? f.webhookId,
          revealedSecret: saved.trigger?.webhookSecret ?? null,
        }));
        setPendingDismiss(true);
        return;
      }
      onClose();
    } catch (e) {
      setError(errMsg(e, "保存失败"));
    } finally {
      setSubmitting(false);
    }
  };

  const onClear = async () => {
    if (!hasTrigger || clearing) return;
    if (!window.confirm("确定清除定时 / Webhook？之后只剩「跑一次」。")) return;
    setClearing(true);
    setError(null);
    try {
      const saved = await deleteWorkflowTrigger(workflow.id);
      onSaved(saved);
      onClose();
    } catch (e) {
      setError(errMsg(e, "清除失败"));
    } finally {
      setClearing(false);
    }
  };

  const onRotate = async () => {
    if (rotating) return;
    if (!window.confirm("轮换后旧密钥立即失效。确定生成新密钥？")) return;
    setRotating(true);
    setError(null);
    try {
      const result = await rotateWorkflowTriggerSecret(workflow.id);
      setForm((f) => ({
        ...f,
        revealedSecret: result.webhookSecret,
        webhookUrl: result.webhookUrl ?? f.webhookUrl,
        webhookId: result.webhookId ?? f.webhookId,
      }));
    } catch (e) {
      setError(errMsg(e, "轮换密钥失败"));
    } finally {
      setRotating(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) requestClose();
      }}
    >
      <DialogContent
        className="max-w-md"
        onPointerDownOutside={(e) => {
          if (pendingDismiss) e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          if (pendingDismiss) e.preventDefault();
        }}
      >
        <DialogTitle>设为定时 · {workflow.name}</DialogTitle>
        <DialogDescription>
          定时与 Webhook
          互斥。只绑云端文件夹；到点按这张图跑，不必再手点「跑一次」。
        </DialogDescription>

        <div className="mt-4 space-y-3">
          {foldersError ? (
            <p className="text-xs text-muted-foreground">
              读不到文件夹列表（{foldersError}
              ），暂时无法确认可用的云端文件夹。
            </p>
          ) : (
            noCloud && (
              <p className="text-xs text-primary">
                没有可用的云端文件夹。请先在「文件」页的「我的文件」里新建一个，任务不能绑定本机文件夹。
              </p>
            )
          )}

          <fieldset disabled={pendingDismiss}>
            <legend className="mb-1 block text-xs text-muted-foreground">
              触发方式
            </legend>
            <SegmentedControl
              aria-label="触发方式"
              value={form.kind}
              onChange={(kind) => setForm((f) => applyKind(f, kind))}
              items={TRIGGER_KIND_ORDER.map((kind) => ({
                value: kind,
                label: TRIGGER_KIND_LABELS[kind],
              }))}
            />
          </fieldset>

          {form.kind === "schedule" && (
            <>
              <label className="block">
                <span className="mb-1 block text-xs text-muted-foreground">
                  周期
                </span>
                <select
                  className={SELECT_CLASS}
                  value={form.schedulePreset}
                  disabled={pendingDismiss}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      schedulePreset: e.target.value as SchedulePreset,
                    }))
                  }
                >
                  {SCHEDULE_PRESET_ORDER.map((id) => (
                    <option key={id} value={id}>
                      {SCHEDULE_PRESET_LABELS[id]}
                    </option>
                  ))}
                </select>
              </label>
              {form.schedulePreset === "custom" && (
                <label className="block" htmlFor="wf-trigger-cron">
                  <span className="mb-1 block text-xs text-muted-foreground">
                    Cron 表达式
                  </span>
                  <Input
                    id="wf-trigger-cron"
                    className="w-full font-mono"
                    value={form.cron}
                    placeholder="0 9 * * 1"
                    disabled={pendingDismiss}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, cron: e.target.value }))
                    }
                  />
                </label>
              )}
            </>
          )}

          {form.kind === "webhook" && (
            <WebhookCredentialsPanel
              webhookUrl={form.webhookUrl}
              revealedSecret={form.revealedSecret}
              canRotate={hasTrigger && !pendingDismiss}
              rotating={rotating}
              onRotate={() => void onRotate()}
              hint={
                !hasTrigger && !form.revealedSecret
                  ? "保存后将显示 Webhook URL 与一次性密钥，请立即复制保存。"
                  : undefined
              }
            />
          )}

          <label className="block">
            <span className="mb-1 block text-xs text-muted-foreground">
              云端文件夹
            </span>
            <select
              className={SELECT_CLASS}
              value={form.folderId}
              disabled={ready === null || noCloud || pendingDismiss}
              onChange={(e) =>
                setForm((f) => ({ ...f, folderId: e.target.value }))
              }
            >
              {ready === null ? (
                <option value="">
                  {foldersState.status === "loading"
                    ? "加载中…"
                    : "文件夹列表加载失败"}
                </option>
              ) : noCloud ? (
                <option value="">还没有云端文件夹</option>
              ) : (
                ready.items.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))
              )}
            </select>
          </label>

          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-foreground">启用</p>
              <p className="text-xs text-muted-foreground">
                {form.kind === "webhook"
                  ? "关闭后外部 POST 不再开跑（可随时打开）。"
                  : "关闭后不再到点触发（可随时打开）。"}
              </p>
            </div>
            <Switch
              checked={form.enabled}
              disabled={pendingDismiss}
              onCheckedChange={(v) => setForm((f) => ({ ...f, enabled: v }))}
              label="启用触发"
            />
          </div>

          {error && <p className="text-xs text-muted-foreground">{error}</p>}
        </div>

        <div className="mt-4 flex flex-wrap justify-end gap-2">
          {pendingDismiss ? (
            <Button
              size="md"
              icon={<Check size={14} />}
              onClick={() => {
                setPendingDismiss(false);
                onClose();
              }}
            >
              已保存密钥，完成
            </Button>
          ) : (
            <>
              {hasTrigger && (
                <Button
                  variant="neutral"
                  size="md"
                  disabled={clearing || submitting}
                  className="mr-auto"
                  onClick={() => void onClear()}
                >
                  {clearing ? "清除中…" : "清除"}
                </Button>
              )}
              <Button variant="neutral" size="md" onClick={onClose}>
                取消
              </Button>
              <Button
                size="md"
                disabled={!canSubmit || submitting || noCloud}
                icon={
                  submitting ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : undefined
                }
                onClick={() => void submit()}
              >
                保存
              </Button>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function WebhookCredentialsPanel({
  webhookUrl,
  revealedSecret,
  canRotate,
  rotating,
  onRotate,
  hint,
}: {
  webhookUrl: string | null;
  revealedSecret: string | null;
  canRotate: boolean;
  rotating: boolean;
  onRotate: () => void;
  hint?: string;
}) {
  return (
    <div className="space-y-3 rounded-lg border border-border bg-muted/30 p-3">
      <p className="text-xs font-medium text-foreground">Webhook</p>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}

      {webhookUrl ? (
        <div>
          <span className="mb-1 block text-xs text-muted-foreground">
            相对 URL
          </span>
          <div className="flex gap-1.5">
            <Input
              className="min-w-0 flex-1 font-mono text-xs"
              value={webhookUrl}
              readOnly
            />
            <Button
              variant="neutral"
              size="sm"
              icon={<Copy size={14} />}
              onClick={() => void copyField("URL", webhookUrl)}
            >
              复制
            </Button>
          </div>
        </div>
      ) : (
        !revealedSecret && (
          <p className="text-xs text-muted-foreground">
            保存后会显示专属相对路径。
          </p>
        )
      )}

      {revealedSecret ? (
        <div>
          <span className="mb-1 block text-xs text-muted-foreground">
            密钥（仅显示一次）
          </span>
          <div className="flex gap-1.5">
            <Input
              className="min-w-0 flex-1 font-mono text-xs"
              value={revealedSecret}
              readOnly
            />
            <Button
              variant="neutral"
              size="sm"
              icon={<Copy size={14} />}
              onClick={() => void copyField("密钥", revealedSecret)}
            >
              复制
            </Button>
          </div>
          <p className="mt-1 text-xs text-warning">
            离开本页后无法再次查看明文；请立即复制保存。
          </p>
        </div>
      ) : (
        webhookUrl && (
          <p className="text-xs text-muted-foreground">
            密钥明文不可再次查看。需要新密钥请轮换（旧密钥立即失效）。
          </p>
        )
      )}

      {canRotate && (
        <Button
          variant="neutral"
          size="sm"
          disabled={rotating}
          icon={
            rotating ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <KeyRound size={14} />
            )
          }
          onClick={onRotate}
        >
          轮换密钥
        </Button>
      )}
    </div>
  );
}
