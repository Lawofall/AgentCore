import { Button, Input, Textarea } from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { IconButton } from "@/components/ui/icon-button";
import { copyText } from "@/lib/clipboard";
import { notifyError, notifySuccess } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { ApiError } from "@/services/api";
import type { FolderMeta } from "@/services/folders";
import {
  type AutonomyRecipe,
  type PermissionAxes,
  RECIPE_LABELS,
  RECIPE_ORDER,
  axesDetailSummary,
  axesEqual,
  confirmAutoCommandIfNeeded,
  matchRecipe,
  recipeToAxes,
} from "@/services/permissionAxes";
import {
  type CreateStandingTaskInput,
  type PatchStandingTaskInput,
  SCHEDULE_PRESET_LABELS,
  SCHEDULE_PRESET_ORDER,
  type SchedulePreset,
  type StandingTask,
  TRIGGER_KIND_LABELS,
  TRIGGER_KIND_ORDER,
  type TriggerKind,
  createStandingTask,
  localHmFromUtcCron,
  patchStandingTask,
  rotateWebhookSecret,
  utcCronFromLocalHm,
} from "@/services/standingTasks";
import { listWorkflowOptions } from "@/services/workflows";
import { Check, Copy, KeyRound, Loader2, Play, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

const SELECT_CLASS =
  "h-8 w-full rounded-lg border border-border bg-background px-2.5 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-ring";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof ApiError ? (e.serverMessage ?? fallback) : fallback;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export interface StandingTaskFormState {
  name: string;
  triggerKind: TriggerKind;
  schedulePreset: SchedulePreset;
  cron: string;
  folderId: string;
  goal: string;
  /**
   * The task's axes as stored server-side — never coerced onto a recipe.
   * System templates ship a custom tuple on purpose; rewriting it on save
   * would silently widen what the task may do.
   */
  permissionAxes: PermissionAxes;
  enabled: boolean;
  webhookUrl: string | null;
  webhookId: string | null;
  /** Ephemeral one-shot secret from create / rotate. */
  revealedSecret: string | null;
  /** Present for system template tasks. */
  templateKey: string | null;
  /** Local wall-clock for template daily cron. */
  localHour: number;
  localMinute: number;
  includeGlobal: boolean;
  scopeFolderIds: string[];
  lookbackHours: number;
  /** Bound user workflow; empty string = unbound. */
  workflowId: string;
  /**
   * Denormalized name of the bound workflow — display only, so the picker can
   * name the binding before (or without) the options request landing.
   */
  workflowName: string | null;
}

export function emptyStandingTaskForm(
  cloudFolders: FolderMeta[],
): StandingTaskFormState {
  return {
    name: "",
    triggerKind: "schedule",
    schedulePreset: "weekly_mon",
    cron: "",
    folderId: cloudFolders[0]?.id ?? "",
    goal: "",
    permissionAxes: recipeToAxes("less_interrupt"),
    enabled: true,
    webhookUrl: null,
    webhookId: null,
    revealedSecret: null,
    templateKey: null,
    localHour: 9,
    localMinute: 0,
    includeGlobal: true,
    scopeFolderIds: [],
    lookbackHours: 24,
    workflowId: "",
    workflowName: null,
  };
}

export function formFromStandingTask(
  task: StandingTask,
): StandingTaskFormState {
  const local = localHmFromUtcCron(task.cron);
  const cfg = task.templateConfig;
  return {
    name: task.name,
    triggerKind: task.triggerKind,
    schedulePreset: task.schedulePreset ?? "weekly_mon",
    cron: task.cron ?? "",
    folderId: task.folderId,
    goal: task.goal,
    permissionAxes: task.permissionAxes,
    enabled: task.enabled,
    webhookUrl: task.webhookUrl,
    webhookId: task.webhookId,
    revealedSecret: task.webhookSecret,
    templateKey: task.templateKey,
    localHour: local.hour,
    localMinute: local.minute,
    includeGlobal: cfg.includeGlobal ?? true,
    scopeFolderIds: cfg.folderIds ?? [],
    lookbackHours: cfg.lookbackHours ?? 24,
    workflowId: task.workflowId ?? "",
    workflowName: task.workflowName,
  };
}

function applyTriggerKind(
  form: StandingTaskFormState,
  kind: TriggerKind,
): StandingTaskFormState {
  if (kind === form.triggerKind) return form;
  if (kind === "webhook") {
    return {
      ...form,
      triggerKind: "webhook",
      schedulePreset: "weekly_mon",
      cron: "",
    };
  }
  return {
    ...form,
    triggerKind: "schedule",
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
 * 创建/编辑站立任务抽屉（从列表抽出，避免整页内联表单）。
 */
export function StandingTaskEditorDrawer({
  open,
  mode,
  initial,
  taskId,
  cloudFolders,
  foldersError,
  onClose,
  onSaved,
}: {
  open: boolean;
  mode: "create" | "edit";
  initial: StandingTaskFormState;
  taskId: string | null;
  cloudFolders: FolderMeta[];
  /** Set when the folder list request failed — an empty list is not proof of "none". */
  foldersError?: string | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [form, setForm] = useState<StandingTaskFormState>(initial);
  const [submitting, setSubmitting] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** After create with webhook secret: stay open until user dismisses. */
  const [pendingDismiss, setPendingDismiss] = useState(false);
  const [workflowOptions, setWorkflowOptions] = useState<
    Array<{ id: string; name: string }>
  >([]);

  const isTemplate = !!form.templateKey;
  const workflowBound = !isTemplate && !!form.workflowId;

  /** The task's own custom tuple, kept selectable so it can be restored. */
  const customAxes = useMemo(
    () =>
      matchRecipe(initial.permissionAxes) === "custom"
        ? initial.permissionAxes
        : null,
    [initial.permissionAxes],
  );
  const selectedRecipe = matchRecipe(form.permissionAxes);
  const axesChanged = !axesEqual(form.permissionAxes, initial.permissionAxes);

  useEffect(() => {
    if (!open) return;
    setForm(initial);
    setPendingDismiss(false);
    setError(null);
  }, [initial, open]);

  useEffect(() => {
    if (!open || isTemplate) return;
    void listWorkflowOptions()
      .then(setWorkflowOptions)
      .catch(() => setWorkflowOptions([]));
  }, [open, isTemplate]);

  const noCloud = cloudFolders.length === 0;
  const canSubmit = useMemo(() => {
    if (!form.folderId) return false;
    if (isTemplate) {
      if (!form.includeGlobal && form.scopeFolderIds.length === 0) return false;
      const lb = form.lookbackHours;
      if (!Number.isFinite(lb) || lb < 1 || lb > 168) return false;
      return true;
    }
    if (!form.name.trim()) return false;
    // Bound workflow: goal becomes optional per-run supplement.
    if (!workflowBound && !form.goal.trim()) return false;
    if (
      form.triggerKind === "schedule" &&
      form.schedulePreset === "custom" &&
      !form.cron.trim()
    ) {
      return false;
    }
    return true;
  }, [form, isTemplate, workflowBound]);

  const buildCreatePayload = (): CreateStandingTaskInput => {
    const base: CreateStandingTaskInput = {
      name: form.name.trim(),
      triggerKind: form.triggerKind,
      folderId: form.folderId,
      goal: form.goal.trim(),
      permissionAxes: form.permissionAxes,
      enabled: form.enabled,
      workflowId: form.workflowId || null,
    };
    if (form.triggerKind === "schedule") {
      base.schedulePreset = form.schedulePreset;
      base.cron =
        form.schedulePreset === "custom" ? form.cron.trim() || null : null;
    }
    return base;
  };

  /** Untouched axes stay off the wire, so saving can never re-grant. */
  const buildEditPatch = (): PatchStandingTaskInput => {
    const { permissionAxes, ...rest } = buildCreatePayload();
    const patch: PatchStandingTaskInput = { ...rest };
    if (axesChanged) patch.permissionAxes = permissionAxes;
    return patch;
  };

  const buildTemplatePatch = (): PatchStandingTaskInput => {
    const patch: PatchStandingTaskInput = {
      folderId: form.folderId,
      schedulePreset: "custom",
      cron: utcCronFromLocalHm(form.localHour, form.localMinute),
      enabled: form.enabled,
      templateConfig: {
        includeGlobal: form.includeGlobal,
        folderIds: form.scopeFolderIds,
        lookbackHours: form.lookbackHours,
      },
    };
    if (axesChanged) patch.permissionAxes = form.permissionAxes;
    return patch;
  };

  const dismissAfterReveal = async () => {
    setPendingDismiss(false);
    await onSaved();
  };

  const requestClose = () => {
    if (pendingDismiss) {
      void dismissAfterReveal();
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
      if (mode === "create") {
        const payload = buildCreatePayload();
        const created = await createStandingTask(payload);
        if (created.triggerKind === "webhook" && created.webhookSecret) {
          setForm((f) => ({
            ...f,
            webhookUrl: created.webhookUrl,
            webhookId: created.webhookId,
            revealedSecret: created.webhookSecret,
          }));
          setPendingDismiss(true);
          return;
        }
      } else if (taskId) {
        const payload = isTemplate ? buildTemplatePatch() : buildEditPatch();
        const patched = await patchStandingTask(taskId, payload);
        if (patched.triggerKind === "webhook" && patched.webhookSecret) {
          setForm((f) => ({
            ...f,
            webhookUrl: patched.webhookUrl ?? f.webhookUrl,
            webhookId: patched.webhookId ?? f.webhookId,
            revealedSecret: patched.webhookSecret,
          }));
          setPendingDismiss(true);
          return;
        }
      }
      await onSaved();
    } catch (e) {
      setError(errMsg(e, mode === "create" ? "创建失败" : "保存失败"));
    } finally {
      setSubmitting(false);
    }
  };

  const onRotate = async () => {
    if (!taskId || rotating) return;
    if (!window.confirm("轮换后旧密钥立即失效。确定生成新密钥？")) {
      return;
    }
    setRotating(true);
    setError(null);
    try {
      const result = await rotateWebhookSecret(taskId);
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

  const toggleScopeFolder = (folderId: string) => {
    setForm((f) => {
      const has = f.scopeFolderIds.includes(folderId);
      return {
        ...f,
        scopeFolderIds: has
          ? f.scopeFolderIds.filter((id) => id !== folderId)
          : [...f.scopeFolderIds, folderId],
      };
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) requestClose();
      }}
    >
      <DialogContent
        showClose={false}
        className={cn(
          "fixed inset-y-0 right-0 left-auto top-0 flex h-full max-h-none w-full max-w-lg translate-x-0 translate-y-0 flex-col overflow-hidden rounded-none border-y-0 border-r-0 p-0 shadow-lg",
          "data-[state=open]:animate-none",
        )}
        onPointerDownOutside={(e) => {
          if (pendingDismiss) e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          if (pendingDismiss) e.preventDefault();
        }}
      >
        <div className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0">
            <DialogTitle className="text-base font-semibold text-foreground">
              {isTemplate
                ? "配置系统任务"
                : mode === "create"
                  ? "新建任务"
                  : "编辑任务"}
            </DialogTitle>
            <DialogDescription className="mt-1 text-xs text-muted-foreground">
              {isTemplate
                ? "系统任务：目标由系统托管，可配置时间、作用域与落点。"
                : "定时或 Webhook 触发后自动开一轮协作。仅支持云端文件夹。"}
            </DialogDescription>
          </div>
          <IconButton
            size="sm"
            aria-label={pendingDismiss ? "完成" : "关闭"}
            onClick={requestClose}
          >
            <X size={16} />
          </IconButton>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {foldersError ? (
            <p className="text-xs text-muted-foreground">
              读不到文件夹列表（{foldersError}
              ），暂时无法确认可用的云端文件夹。请关闭抽屉重试。
            </p>
          ) : (
            noCloud && (
              <p className="text-xs text-primary">
                没有可用的云端文件夹。请先在「文件」页的「我的文件」里新建一个，任务不能绑定本机文件夹。
              </p>
            )
          )}

          <label className="block" htmlFor="st-name">
            <span className="mb-1 block text-xs text-muted-foreground">
              名称
            </span>
            <Input
              id="st-name"
              className="w-full"
              value={form.name}
              maxLength={120}
              placeholder="例如：周一竞品简报"
              disabled={pendingDismiss || isTemplate}
              readOnly={isTemplate}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            />
          </label>

          {!isTemplate && (
            <fieldset disabled={pendingDismiss}>
              <legend className="mb-1 block text-xs text-muted-foreground">
                触发方式
              </legend>
              <div className="flex flex-wrap gap-2">
                {TRIGGER_KIND_ORDER.map((kind) => (
                  <button
                    key={kind}
                    type="button"
                    className={cn(
                      "rounded-lg border px-3 py-1.5 text-sm transition-colors",
                      form.triggerKind === kind
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border bg-background text-muted-foreground hover:text-foreground",
                    )}
                    onClick={() => setForm((f) => applyTriggerKind(f, kind))}
                  >
                    {TRIGGER_KIND_LABELS[kind]}
                  </button>
                ))}
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                每任务仅一种触发；切换会清空另一方配置。
              </p>
            </fieldset>
          )}

          {isTemplate ? (
            <label className="block" htmlFor="st-local-time">
              <span className="mb-1 block text-xs text-muted-foreground">
                每天触发时间（本地）
              </span>
              <Input
                id="st-local-time"
                type="time"
                className="w-full"
                value={`${pad2(form.localHour)}:${pad2(form.localMinute)}`}
                disabled={pendingDismiss}
                onChange={(e) => {
                  const [h, m] = e.target.value.split(":").map(Number);
                  if (!Number.isFinite(h) || !Number.isFinite(m)) return;
                  setForm((f) => ({
                    ...f,
                    localHour: h,
                    localMinute: m,
                  }));
                }}
              />
              <p className="mt-1 text-xs text-muted-foreground">
                按你本机时区保存；服务端以 UTC cron 调度。
              </p>
            </label>
          ) : (
            form.triggerKind === "schedule" && (
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
                  <label className="block" htmlFor="st-cron">
                    <span className="mb-1 block text-xs text-muted-foreground">
                      Cron 表达式
                    </span>
                    <Input
                      id="st-cron"
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
            )
          )}

          {!isTemplate && form.triggerKind === "webhook" && (
            <WebhookCredentialsPanel
              webhookUrl={form.webhookUrl}
              revealedSecret={form.revealedSecret}
              canRotate={mode === "edit" && !!taskId && !pendingDismiss}
              rotating={rotating}
              onRotate={() => void onRotate()}
              hint={
                mode === "create" && !form.revealedSecret
                  ? "创建后将显示 Webhook URL 与一次性密钥，请立即复制保存。"
                  : undefined
              }
            />
          )}

          <label className="block">
            <span className="mb-1 block text-xs text-muted-foreground">
              {isTemplate ? "报告落点文件夹" : "云端文件夹"}
            </span>
            <select
              className={SELECT_CLASS}
              value={form.folderId}
              disabled={noCloud || pendingDismiss}
              onChange={(e) =>
                setForm((f) => ({ ...f, folderId: e.target.value }))
              }
            >
              {cloudFolders.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
            {isTemplate && (
              <p className="mt-1 text-xs text-muted-foreground">
                复盘报告与文档草稿写入此文件夹。
              </p>
            )}
          </label>

          {isTemplate ? (
            <>
              <div className="rounded-lg border border-border bg-muted/30 px-3 py-2.5">
                <p className="text-xs font-medium text-foreground">任务说明</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  每天自动检索近期对话，整理成管家式复盘报告；你确认后才更新记忆、写入文档草稿或采纳规则建议。目标文案由系统托管，不可改。
                </p>
              </div>

              <fieldset disabled={pendingDismiss} className="space-y-2">
                <legend className="mb-1 block text-xs text-muted-foreground">
                  复盘作用域
                </legend>
                <label className="flex items-center gap-2 text-sm text-foreground">
                  <input
                    type="checkbox"
                    className="size-4 rounded-lg border-border"
                    checked={form.includeGlobal}
                    onChange={(e) =>
                      setForm((f) => ({
                        ...f,
                        includeGlobal: e.target.checked,
                      }))
                    }
                  />
                  包含全局裸聊
                </label>
                <div className="space-y-1.5">
                  <p className="text-xs text-muted-foreground">
                    一并复盘的云端文件夹
                  </p>
                  {cloudFolders.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      {foldersError ? "文件夹列表未加载成功" : "暂无云端文件夹"}
                    </p>
                  ) : (
                    <ul className="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
                      {cloudFolders.map((f) => (
                        <li key={f.id}>
                          <label className="flex items-center gap-2 text-sm text-foreground">
                            <input
                              type="checkbox"
                              className="size-4 rounded-lg border-border"
                              checked={form.scopeFolderIds.includes(f.id)}
                              onChange={() => toggleScopeFolder(f.id)}
                            />
                            <span className="truncate">{f.name}</span>
                          </label>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                {!form.includeGlobal && form.scopeFolderIds.length === 0 && (
                  <p className="text-xs text-muted-foreground">
                    请至少勾选「全局裸聊」或一个云端文件夹。
                  </p>
                )}
              </fieldset>

              <label className="block" htmlFor="st-lookback">
                <span className="mb-1 block text-xs text-muted-foreground">
                  回看时长（小时）
                </span>
                <Input
                  id="st-lookback"
                  type="number"
                  min={1}
                  max={168}
                  className="w-full"
                  value={form.lookbackHours}
                  disabled={pendingDismiss}
                  onChange={(e) => {
                    const n = Number(e.target.value);
                    setForm((f) => ({
                      ...f,
                      lookbackHours: Number.isFinite(n) ? n : f.lookbackHours,
                    }));
                  }}
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  默认 24，最大 168（7 天）。
                </p>
              </label>
            </>
          ) : (
            <>
              <label className="block">
                <span className="mb-1 block text-xs text-muted-foreground">
                  绑定工作流（可选）
                </span>
                <select
                  className={SELECT_CLASS}
                  value={form.workflowId}
                  disabled={pendingDismiss}
                  onChange={(e) =>
                    setForm((f) => ({
                      ...f,
                      workflowId: e.target.value,
                      workflowName:
                        workflowOptions.find((w) => w.id === e.target.value)
                          ?.name ?? null,
                    }))
                  }
                >
                  <option value="">不绑定 — 到点按目标文案开跑</option>
                  {/* 选项还没到（或列表请求挂了）时，别把已绑定的工作流显示成空白 */}
                  {form.workflowId &&
                    !workflowOptions.some((w) => w.id === form.workflowId) && (
                      <option value={form.workflowId}>
                        {form.workflowName ?? "已绑定的工作流"}
                      </option>
                    )}
                  {workflowOptions.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-muted-foreground">
                  绑定后结构以工作流为准；可在{" "}
                  <Link
                    to={APP_PATHS.toolbox.workflows.root}
                    className="text-primary underline-offset-2 hover:underline"
                  >
                    工具箱 · 工作流
                  </Link>{" "}
                  编辑画布。
                </p>
              </label>

              <label className="block" htmlFor="st-goal">
                <span className="mb-1 block text-xs text-muted-foreground">
                  {workflowBound ? "本轮补充（可选）" : "目标"}
                </span>
                <Textarea
                  id="st-goal"
                  className="w-full text-sm"
                  rows={4}
                  value={form.goal}
                  maxLength={4000}
                  disabled={pendingDismiss}
                  placeholder={
                    workflowBound
                      ? "空则只按工作流图跑；有则作为本轮附加上下文，不改图。"
                      : form.triggerKind === "webhook"
                        ? "常驻交代：收到外部事件后要完成什么？事件正文会追加到本轮上下文。"
                        : "到点要完成什么？例如：汇总本周竞品动态与风险，给出三条行动建议。"
                  }
                  onChange={(e) =>
                    setForm((f) => ({ ...f, goal: e.target.value }))
                  }
                />
              </label>
            </>
          )}

          <label className="block">
            <span className="mb-1 block text-xs text-muted-foreground">
              自主度
            </span>
            <select
              className={SELECT_CLASS}
              value={selectedRecipe}
              disabled={pendingDismiss}
              onChange={(e) => {
                const picked = e.target.value as AutonomyRecipe | "custom";
                const next =
                  picked === "custom" ? customAxes : recipeToAxes(picked);
                if (!next) return;
                if (!confirmAutoCommandIfNeeded(form.permissionAxes, next)) {
                  return;
                }
                setForm((f) => ({ ...f, permissionAxes: next }));
              }}
            >
              {customAxes && (
                <option value="custom">
                  自定义 — {axesDetailSummary(customAxes)}
                </option>
              )}
              {RECIPE_ORDER.map((id) => (
                <option key={id} value={id}>
                  {RECIPE_LABELS[id].short} — {RECIPE_LABELS[id].description}
                </option>
              ))}
            </select>
            {selectedRecipe === "custom" && (
              <p className="mt-1 text-xs text-muted-foreground">
                {isTemplate
                  ? "系统任务自带的权限组合，不改就保持原样；换成内置配方会覆盖它。"
                  : "当前是自定义权限组合，不改就保持原样；换成内置配方会覆盖它。"}
              </p>
            )}
          </label>

          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-foreground">启用</p>
              <p className="text-xs text-muted-foreground">
                {isTemplate
                  ? "关闭后不再到点复盘（可随时打开）。"
                  : form.triggerKind === "webhook"
                    ? "关闭后外部 POST 不再开跑（可随时打开）。"
                    : "关闭后不再到点触发（可随时打开）。"}
              </p>
            </div>
            <Switch
              checked={form.enabled}
              disabled={pendingDismiss}
              onCheckedChange={(v) => setForm((f) => ({ ...f, enabled: v }))}
              label="启用任务"
            />
          </div>

          {error && <p className="text-xs text-muted-foreground">{error}</p>}
        </div>

        <div className="flex justify-end gap-2 border-t border-border px-5 py-4">
          {pendingDismiss ? (
            <Button
              size="md"
              icon={<Check size={14} />}
              onClick={() => void dismissAfterReveal()}
            >
              已保存密钥，完成
            </Button>
          ) : (
            <>
              <Button variant="neutral" size="md" onClick={onClose}>
                取消
              </Button>
              <Button
                size="md"
                disabled={!canSubmit || submitting || noCloud}
                icon={
                  submitting ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : mode === "create" ? (
                    <Play size={14} />
                  ) : undefined
                }
                onClick={() => void submit()}
              >
                {mode === "create" ? "创建" : "保存"}
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
      <p className="text-xs font-medium text-foreground">Webhook 凭证</p>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}

      {webhookUrl ? (
        <div>
          <span className="mb-1 block text-xs text-muted-foreground">URL</span>
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
              onClick={() => void copyField(" URL", webhookUrl)}
            >
              复制
            </Button>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            外部系统 POST 到此地址；鉴权用 Bearer 密钥或{" "}
            <code className="font-mono text-xs">
              X-AgentCore-Webhook-Secret
            </code>
            。
          </p>
        </div>
      ) : (
        !revealedSecret && (
          <p className="text-xs text-muted-foreground">
            创建成功后会显示专属 URL。
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
