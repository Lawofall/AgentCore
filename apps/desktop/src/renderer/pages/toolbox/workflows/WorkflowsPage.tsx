import { Badge, Button, EmptyHint } from "@/components/ui";
import { notifyError, notifySuccess } from "@/lib/toast";
import { InventoryRow, InventoryRowAction } from "@/pages/toolbox/InventoryRow";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { ApiError } from "@/services/api";
import { emptyWorkflowDefinition } from "@/services/workflowDefinition";
import {
  type WorkflowStoreListing,
  listInstalledWorkflows,
  listMyWorkflowListings,
  publishWorkflow,
  publishWorkflowVersion,
  unpublishWorkflow,
} from "@/services/workflowStore";
import {
  type UserWorkflow,
  createWorkflow,
  deleteWorkflow,
  listWorkflows,
  triggerSummary,
} from "@/services/workflows";
import {
  CalendarClock,
  Loader2,
  Play,
  Plus,
  Trash2,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { RunWorkflowDialog } from "./RunWorkflowDialog";
import { WorkflowTriggerDialog } from "./WorkflowTriggerDialog";

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

function rowMeta(w: UserWorkflow): string {
  const stepCount = w.definition.nodes.filter(
    (n) => n.kind === "agent_step",
  ).length;
  const gateCount = w.definition.nodes.filter(
    (n) => n.kind === "human_gate",
  ).length;
  const slotCount = w.definition.slots?.length ?? 0;
  const parts = [
    `${stepCount} 步骤 · ${gateCount} 关卡`,
    slotCount > 0 ? `${slotCount} 可换参数` : null,
    `v${w.version}`,
    `更新 ${formatUpdated(w.updatedAt)}`,
  ];
  const summary = triggerSummary(w.trigger);
  if (summary) parts.push(summary);
  if (w.trigger?.lastError) parts.push("上次没跑成");
  return parts.filter(Boolean).join(" · ");
}

/**
 * 我的 · 工作流。官方模板在市场。
 *
 * 报错口径：列表加载失败走 inline 文案；行内一次性动作（新建 / 删除）走 toast。
 */
export function WorkflowsPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<UserWorkflow[] | null>(null);
  const [listings, setListings] = useState<WorkflowStoreListing[]>([]);
  const [installedCopyIds, setInstalledCopyIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [listError, setListError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [runTarget, setRunTarget] = useState<UserWorkflow | null>(null);
  const [triggerTarget, setTriggerTarget] = useState<UserWorkflow | null>(null);

  const loadMine = useCallback(async () => {
    setListError(null);
    try {
      const [list, mineListings, installed] = await Promise.all([
        listWorkflows(),
        listMyWorkflowListings(),
        listInstalledWorkflows(),
      ]);
      setItems(list);
      setListings(mineListings);
      setInstalledCopyIds(
        new Set(
          installed
            .map((row) => row.installWorkflowId)
            .filter((id): id is string => Boolean(id)),
        ),
      );
    } catch (e) {
      setListError(errMsg(e, "加载工作流失败"));
    }
  }, []);

  useEffect(() => {
    void loadMine();
  }, [loadMine]);

  const replaceItem = (next: UserWorkflow) => {
    setItems((prev) => (prev ?? []).map((w) => (w.id === next.id ? next : w)));
    setTriggerTarget((cur) => (cur?.id === next.id ? next : cur));
  };

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

  const listingFor = (w: UserWorkflow) =>
    listings.find((row) => row.workflowId === w.id) ?? null;

  const onPublish = async (w: UserWorkflow) => {
    if (busyId) return;
    setBusyId(w.id);
    try {
      const existing = listingFor(w);
      if (existing?.status === "taken_down") return;
      if (existing?.status === "published") {
        await publishWorkflowVersion(existing.id);
      } else {
        await publishWorkflow(w.id);
      }
      setListings(await listMyWorkflowListings());
      notifySuccess("已上架");
    } catch (e) {
      notifyError(e, "上架失败");
    } finally {
      setBusyId(null);
    }
  };

  const onUnpublish = async (w: UserWorkflow) => {
    if (busyId) return;
    const existing = listingFor(w);
    if (!existing) return;
    setBusyId(w.id);
    try {
      await unpublishWorkflow(existing.id);
      setListings(await listMyWorkflowListings());
      notifySuccess("已下架");
    } catch (e) {
      notifyError(e, "下架失败");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <div className="flex justify-end">
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
      </div>

      <div className="mt-4 space-y-1">
        {listError ? (
          <div className="flex flex-wrap items-center gap-3 px-3 py-2">
            <p className="min-w-0 flex-1 text-sm text-muted-foreground">
              {listError}
            </p>
            <Button variant="neutral" size="sm" onClick={() => void loadMine()}>
              重试
            </Button>
          </div>
        ) : items === null ? (
          <div className="flex items-center gap-2 px-3 text-sm text-muted-foreground">
            <Loader2 size={16} className="animate-spin" />
            加载中…
          </div>
        ) : items.length === 0 ? (
          <EmptyHint
            className="py-10"
            title="还没有工作流"
            action={
              <Button
                variant="neutral"
                size="sm"
                onClick={() => navigate(APP_PATHS.toolbox.market)}
              >
                去市场
              </Button>
            }
          />
        ) : (
          items.map((w) => {
            const busy = busyId === w.id;
            const listing = listingFor(w);
            const fromMarket = installedCopyIds.has(w.id);
            const canPublish = !fromMarket && listing?.status !== "taken_down";
            const canUnpublish = !fromMarket && listing?.status === "published";
            return (
              <InventoryRow
                key={w.id}
                title={w.name}
                badges={
                  <>
                    {listing?.status === "published" ? (
                      <Badge tone="muted">已上架</Badge>
                    ) : null}
                    {listing?.status === "taken_down" ? (
                      <Badge tone="muted">平台已下架</Badge>
                    ) : null}
                    {fromMarket ? <Badge tone="muted">市场</Badge> : null}
                  </>
                }
                meta={rowMeta(w)}
                detail={
                  w.description ? (
                    <p className="mt-1 line-clamp-1 text-xs text-muted-foreground">
                      {w.description}
                    </p>
                  ) : null
                }
                onOpen={() => navigate(APP_PATHS.toolbox.workflows.edit(w.id))}
                actions={
                  <>
                    <InventoryRowAction
                      label="跑一次"
                      disabled={busy}
                      onClick={() => setRunTarget(w)}
                    >
                      <Play size={14} />
                    </InventoryRowAction>
                    <InventoryRowAction
                      label="设为定时"
                      disabled={busy}
                      onClick={() => setTriggerTarget(w)}
                    >
                      <CalendarClock size={14} />
                    </InventoryRowAction>
                    {canPublish ? (
                      <InventoryRowAction
                        label="上架"
                        disabled={busy}
                        onClick={() => void onPublish(w)}
                      >
                        <Upload size={14} />
                      </InventoryRowAction>
                    ) : null}
                    {canUnpublish ? (
                      <InventoryRowAction
                        label="下架"
                        disabled={busy}
                        onClick={() => void onUnpublish(w)}
                      >
                        <Upload size={14} className="rotate-180" />
                      </InventoryRowAction>
                    ) : null}
                    <InventoryRowAction
                      label="删除"
                      disabled={busy}
                      destructive
                      onClick={() => void onDelete(w)}
                    >
                      {busy ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : (
                        <Trash2 size={14} />
                      )}
                    </InventoryRowAction>
                  </>
                }
              />
            );
          })
        )}
      </div>

      {runTarget && (
        <RunWorkflowDialog
          open
          workflowId={runTarget.id}
          workflowName={runTarget.name}
          definition={runTarget.definition}
          source={runTarget.source}
          onSlotsSuggested={(next) =>
            setItems((prev) =>
              (prev ?? []).map((w) => (w.id === next.id ? next : w)),
            )
          }
          onClose={() => setRunTarget(null)}
        />
      )}

      {triggerTarget && (
        <WorkflowTriggerDialog
          open
          workflow={triggerTarget}
          onSaved={replaceItem}
          onClose={() => setTriggerTarget(null)}
        />
      )}
    </div>
  );
}
