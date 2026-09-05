import { PageContainer } from "@/components/layout/PageContainer";
import { Button, Card, EmptyHint, PageHeader } from "@/components/ui";
import { notifyError } from "@/lib/toast";
import { scheduleFromWorkflowPath } from "@/pages/toolbox/automations/scheduleFromWorkflow";
import { APP_PATHS, TOOLBOX_PAGE_BACK } from "@/pages/toolbox/manual/paths";
import { ApiError } from "@/services/api";
import { emptyWorkflowDefinition } from "@/services/workflowDefinition";
import {
  type UserWorkflow,
  type WorkflowTemplate,
  createWorkflow,
  deleteWorkflow,
  listWorkflowTemplates,
  listWorkflows,
} from "@/services/workflows";
import {
  CalendarClock,
  Copy,
  Loader2,
  Pencil,
  Play,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { OfficialTemplateGuide } from "./OfficialTemplateGuide";
import { RunWorkflowDialog } from "./RunWorkflowDialog";
import { UseTemplateDialog } from "./UseTemplateDialog";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof ApiError ? (e.serverMessage ?? fallback) : fallback;
}

function formatUpdated(iso: string): string {
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
 * 工具箱 · 工作流列表（高级资产入口）。
 * 两区：官方模板（只读 · 使用=复制为我的） / 我的工作流（编辑/跑/删）。
 *
 * 报错口径：区域加载失败走该区域的 inline 文案（用户要看到哪块没加载出来）；
 * 行内一次性动作（新建 / 删除）走 toast。同一次失败只走一个通道，不叠加。
 */
export function WorkflowsPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<UserWorkflow[] | null>(null);
  const [templates, setTemplates] = useState<WorkflowTemplate[] | null>(null);
  const [templatesHint, setTemplatesHint] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [runTarget, setRunTarget] = useState<UserWorkflow | null>(null);
  const [useTarget, setUseTarget] = useState<WorkflowTemplate | null>(null);

  const loadMine = useCallback(async () => {
    setListError(null);
    try {
      const list = await listWorkflows();
      setItems(list);
    } catch (e) {
      // 保留旧 items：渲染时 listError 优先，避免把「请求失败」显示成「还没有工作流」。
      setListError(errMsg(e, "加载工作流失败"));
    }
  }, []);

  const loadTemplates = useCallback(async () => {
    try {
      const list = await listWorkflowTemplates();
      setTemplates(list);
      setTemplatesHint(null);
      // Empty after a successful call = backend ready but catalog empty, or 404
      // degraded to []. Hide section when empty (see render).
    } catch (e) {
      // 只影响官方模板区：「我的工作流」照常渲染，这里如实报错并给重试。
      setTemplatesHint(errMsg(e, "官方模板加载失败"));
    }
  }, []);

  useEffect(() => {
    void loadMine();
    void loadTemplates();
  }, [loadMine, loadTemplates]);

  const onCreate = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const created = await createWorkflow({
        name: "未命名工作流",
        definition: emptyWorkflowDefinition(),
      });
      navigate(APP_PATHS.toolbox.workflows.edit(created.id));
    } catch (e) {
      notifyError(e, "创建失败");
    } finally {
      setCreating(false);
    }
  };

  const onDelete = async (w: UserWorkflow) => {
    if (!window.confirm(`确定删除「${w.name}」？`)) return;
    setBusyId(w.id);
    try {
      await deleteWorkflow(w.id);
      setItems((prev) => (prev ?? []).filter((x) => x.id !== w.id));
    } catch (e) {
      notifyError(e, "删除失败");
    } finally {
      setBusyId(null);
    }
  };

  const showOfficial = (templates?.length ?? 0) > 0 || !!templatesHint;

  return (
    <PageContainer width="canvas">
      <PageHeader
        title="工作流"
        back={TOOLBOX_PAGE_BACK}
        action={
          <Button
            size="md"
            disabled={creating}
            icon={
              creating ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Plus size={14} />
              )
            }
            onClick={() => void onCreate()}
          >
            新建工作流
          </Button>
        }
      />

      {showOfficial && (
        <section className="mt-6 space-y-3">
          <p className="text-xs font-medium text-muted-foreground">官方模板</p>
          <OfficialTemplateGuide templates={templates ?? []} />
          {templatesHint && (
            <div className="flex flex-wrap items-center gap-3">
              <p className="min-w-0 flex-1 text-xs text-muted-foreground">
                {templatesHint}
              </p>
              <Button
                variant="neutral"
                size="sm"
                onClick={() => void loadTemplates()}
              >
                重试
              </Button>
            </div>
          )}
          {templates?.map((tpl) => (
            <Card key={tpl.id} className="px-4 py-3">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Sparkles size={16} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-medium text-foreground">
                      {tpl.title}
                    </p>
                    <span className="rounded-lg bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
                      官方
                    </span>
                  </div>
                  {tpl.summary ? (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {tpl.summary}
                    </p>
                  ) : null}
                </div>
                <Button
                  size="md"
                  variant="neutral"
                  icon={<Copy size={14} />}
                  onClick={() => setUseTarget(tpl)}
                >
                  使用
                </Button>
              </div>
            </Card>
          ))}
        </section>
      )}

      <section className="mt-6">
        <div className="mb-3 flex items-center justify-between gap-3">
          <p className="text-xs font-medium text-muted-foreground">
            我的工作流
          </p>
        </div>
        <div className="space-y-3">
          {listError ? (
            <Card className="flex flex-wrap items-center gap-3 p-4">
              <p className="min-w-0 flex-1 text-sm text-muted-foreground">
                {listError}
              </p>
              <Button
                variant="neutral"
                size="sm"
                onClick={() => void loadMine()}
              >
                重试
              </Button>
            </Card>
          ) : items === null ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 size={16} className="animate-spin" />
              加载中…
            </div>
          ) : items.length === 0 ? (
            <EmptyHint
              className="py-10"
              title="还没有工作流"
              hint="可从上方官方模板「使用」复制一份，或新建空白图；保存后可手动跑一次，也可用卡片上的「设为定时」变成到点自动跑的任务。"
            />
          ) : (
            items.map((w) => {
              const stepCount = w.definition.nodes.filter(
                (n) => n.kind === "agent_step",
              ).length;
              const gateCount = w.definition.nodes.filter(
                (n) => n.kind === "human_gate",
              ).length;
              const slotCount = w.definition.slots?.length ?? 0;
              const busy = busyId === w.id;
              return (
                <Card
                  key={w.id}
                  className="flex flex-wrap items-center gap-3 p-4"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-foreground">
                      {w.name}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {stepCount} 步骤 · {gateCount} 关卡
                      {slotCount > 0 ? ` · ${slotCount} 可换参数` : ""} · v
                      {w.version} · 更新 {formatUpdated(w.updatedAt)}
                    </p>
                    {w.description ? (
                      <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">
                        {w.description}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <Button
                      variant="neutral"
                      size="sm"
                      icon={<Play size={14} />}
                      disabled={busy}
                      onClick={() => setRunTarget(w)}
                    >
                      跑一次
                    </Button>
                    <Button
                      variant="neutral"
                      size="sm"
                      icon={<CalendarClock size={14} />}
                      disabled={busy}
                      title="到「自动化」新建一条绑定本工作流的定时任务"
                      onClick={() => navigate(scheduleFromWorkflowPath(w))}
                    >
                      设为定时
                    </Button>
                    <Button
                      variant="neutral"
                      size="sm"
                      icon={<Pencil size={14} />}
                      disabled={busy}
                      onClick={() =>
                        navigate(APP_PATHS.toolbox.workflows.edit(w.id))
                      }
                    >
                      编辑
                    </Button>
                    <Button
                      variant="neutral"
                      size="sm"
                      icon={
                        busy ? (
                          <Loader2 size={14} className="animate-spin" />
                        ) : (
                          <Trash2 size={14} />
                        )
                      }
                      disabled={busy}
                      onClick={() => void onDelete(w)}
                    >
                      删除
                    </Button>
                  </div>
                </Card>
              );
            })
          )}
        </div>
      </section>

      {runTarget && (
        <RunWorkflowDialog
          open
          workflowId={runTarget.id}
          workflowName={runTarget.name}
          definition={runTarget.definition}
          source={runTarget.source}
          // 抽槽改的是服务端那份：列表跟着换，卡片上的「N 可换参数」才不是旧数，
          // 下次开对话框也直接用已有槽位。开着的这次仍用 `runTarget` 那份，不抖动。
          onSlotsSuggested={(next) =>
            setItems((prev) =>
              (prev ?? []).map((w) => (w.id === next.id ? next : w)),
            )
          }
          onClose={() => setRunTarget(null)}
        />
      )}

      <UseTemplateDialog
        open={!!useTarget}
        template={useTarget}
        onClose={() => setUseTarget(null)}
      />
    </PageContainer>
  );
}
