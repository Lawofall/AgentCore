import { CanvasShell } from "@/components/layout/CanvasShell";
import { Button, Input } from "@/components/ui";
import { notifySuccess } from "@/lib/toast";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { ApiError } from "@/services/api";
import {
  type WorkflowDefinition,
  validateWorkflowDefinition,
} from "@/services/workflowDefinition";
import { workflowTurnPath } from "@/services/workflowSource";
import {
  type UserWorkflow,
  getWorkflow,
  patchWorkflow,
} from "@/services/workflows";
import { Loader2, MessageSquare, Play, Save } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { RunWorkflowDialog } from "./RunWorkflowDialog";
import { WorkflowCanvas } from "./WorkflowCanvas";
import { WorkflowNodeInspector } from "./WorkflowNodeInspector";
import { WorkflowSlotsPanel } from "./WorkflowSlotsPanel";

function errMsg(e: unknown, fallback: string): string {
  return e instanceof ApiError ? (e.serverMessage ?? fallback) : fallback;
}

const TITLE_FIELD_CLASS =
  "min-w-0 max-w-xs flex-1 rounded-lg bg-transparent px-2 py-1 text-sm font-medium text-foreground outline-none hover:bg-accent focus:bg-accent";

/** P2 预留：从白板进来时 `?from=board&boardId=` 回到那张板。 */
function editorBack(search: URLSearchParams): {
  to: string;
  ariaLabel: string;
} {
  const from = search.get("from");
  const boardId = search.get("boardId");
  if (
    from === "board" &&
    boardId &&
    !boardId.includes("/") &&
    !boardId.includes("\\")
  ) {
    return { to: `/whiteboard/${boardId}`, ariaLabel: "返回白板" };
  }
  return {
    to: APP_PATHS.toolbox.workflows.root,
    ariaLabel: "返回工作流列表",
  };
}

/**
 * 工作流定义态画布编辑页（与协作图路由隔离）。
 *
 * 报错口径：页面自带 inline 错误位，加载 / 保存失败都只走 inline，不再另弹 toast；
 * toast 只留给保存成功这类瞬时反馈。
 */
export function WorkflowEditorPage() {
  const { workflowId = "" } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const back = editorBack(search);
  const [workflow, setWorkflow] = useState<UserWorkflow | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [definition, setDefinition] = useState<WorkflowDefinition | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runOpen, setRunOpen] = useState(false);
  // 画布上那份最后与服务端对齐过的 definition：引用还是它 = 用户没动过画布。
  const syncedDef = useRef<WorkflowDefinition | null>(null);
  // Generation + source id: same-component route switch must not apply A onto B,
  // and save must not PATCH unless the route still matches the loaded workflow.
  const loadGenRef = useRef(0);
  const loadedSourceIdRef = useRef<string | null>(null);

  const load = useCallback(async () => {
    if (!workflowId) return;
    const requestedId = workflowId;
    const gen = ++loadGenRef.current;
    loadedSourceIdRef.current = null;
    setLoading(true);
    setSaving(false);
    setError(null);
    setSelectedId(null);
    try {
      const w = await getWorkflow(requestedId);
      if (gen !== loadGenRef.current) return;
      loadedSourceIdRef.current = w.id;
      setWorkflow(w);
      setName(w.name);
      setDescription(w.description ?? "");
      setDefinition(w.definition);
      syncedDef.current = w.definition;
    } catch (e) {
      if (gen !== loadGenRef.current) return;
      setError(errMsg(e, "加载工作流失败"));
      setWorkflow(null);
    } finally {
      if (gen === loadGenRef.current) setLoading(false);
    }
  }, [workflowId]);

  useEffect(() => {
    void load();
  }, [load]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: deps 故意含 workflowId，切换时跑 cleanup bump gen
  useEffect(() => {
    return () => {
      loadGenRef.current += 1;
      loadedSourceIdRef.current = null;
    };
  }, [workflowId]);

  const issues = useMemo(
    () => (definition ? validateWorkflowDefinition(definition) : []),
    [definition],
  );

  const save = async () => {
    const sourceId = loadedSourceIdRef.current;
    if (!workflowId || !definition || saving) return;
    if (!sourceId || sourceId !== workflowId) return;
    if (!name.trim()) {
      setError("请填写名称");
      return;
    }
    if (issues.length > 0) {
      setError(issues[0]?.message ?? "定义校验未通过");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const next = await patchWorkflow(sourceId, {
        name: name.trim(),
        description: description.trim() || null,
        definition,
      });
      if (loadedSourceIdRef.current !== sourceId) return;
      setWorkflow(next);
      syncedDef.current = definition;
      notifySuccess("工作流已保存");
    } catch (e) {
      if (loadedSourceIdRef.current !== sourceId) return;
      setError(errMsg(e, "保存失败"));
    } finally {
      if (loadedSourceIdRef.current === sourceId) setSaving(false);
    }
  };

  /**
   * 跑一次时按需抽出的槽位：服务端那份 definition 已经被换掉（任务文本里多了占位符）。
   * 画布没动过就跟着换，否则用户回头一点保存，就把刚抽出来的槽位连同占位符 PATCH 没了；
   * 动过就只更新已存快照——他手上的编辑不能被这个后台结果盖掉。
   */
  const adoptSuggestedSlots = useCallback((next: UserWorkflow) => {
    if (loadedSourceIdRef.current !== next.id) return;
    setDefinition((cur) => (cur === syncedDef.current ? next.definition : cur));
    syncedDef.current = next.definition;
    setWorkflow(next);
  }, []);

  const goBack = () => navigate(back.to);

  if (loading) {
    return (
      <CanvasShell backAriaLabel={back.ariaLabel} onBack={goBack} title={null}>
        <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 size={16} className="animate-spin" />
          加载中…
        </div>
      </CanvasShell>
    );
  }

  if (!workflow || !definition) {
    return (
      <CanvasShell backAriaLabel={back.ariaLabel} onBack={goBack} title={null}>
        <div className="flex h-full items-center justify-center px-6">
          <p className="text-sm text-muted-foreground">
            {error ?? "工作流不存在"}
          </p>
        </div>
      </CanvasShell>
    );
  }

  // 固化来源带着原对话与消息：给一条回去看「它是从哪一轮存下来的」的路。
  const turnPath = workflowTurnPath(workflow.source);
  const selectedNode =
    definition.nodes.find((n) => n.id === selectedId) ?? null;
  const banner =
    issues.length > 0 || error ? (
      <div className="shrink-0 space-y-1 border-b border-border px-3 py-2">
        {issues.length > 0 ? (
          <ul className="space-y-1 text-xs text-warning">
            {issues.slice(0, 4).map((issue) => (
              <li key={`${issue.code}-${issue.nodeId ?? ""}`}>
                {issue.message}
              </li>
            ))}
          </ul>
        ) : null}
        {error ? (
          <p className="text-xs text-muted-foreground">{error}</p>
        ) : null}
      </div>
    ) : null;

  return (
    <>
      <CanvasShell
        backAriaLabel={back.ariaLabel}
        onBack={goBack}
        title={
          <input
            value={name}
            maxLength={120}
            onChange={(e) => setName(e.target.value)}
            aria-label="工作流标题"
            className={TITLE_FIELD_CLASS}
          />
        }
        status={`v${workflow.version}`}
        actions={
          <>
            {turnPath ? (
              <Button
                variant="ghost"
                size="sm"
                icon={<MessageSquare size={14} />}
                title="这个工作流是从一轮协作存下来的，回去看看那一轮"
                onClick={() => navigate(turnPath)}
              >
                回到原对话
              </Button>
            ) : null}
            <Button
              variant="neutral"
              size="md"
              icon={<Play size={14} />}
              onClick={() => setRunOpen(true)}
            >
              跑一次
            </Button>
            <Button
              size="md"
              disabled={saving}
              icon={
                saving ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Save size={14} />
                )
              }
              onClick={() => void save()}
            >
              保存
            </Button>
          </>
        }
        banner={banner}
      >
        <div className="absolute inset-0">
          <WorkflowCanvas
            definition={definition}
            selectedId={selectedId}
            onChange={setDefinition}
            onSelect={setSelectedId}
            className="h-full min-h-0"
          />
        </div>
        {selectedNode ? (
          <aside
            aria-label="工作流属性"
            className="absolute inset-y-3 right-3 z-10 flex w-[280px] flex-col overflow-hidden rounded-xl border border-border bg-card shadow-lg"
          >
            <div className="min-h-0 flex-1 divide-y divide-border overflow-y-auto">
              <label className="block p-4" htmlFor="wf-desc">
                <span className="mb-1 block text-xs text-muted-foreground">
                  说明（可选）
                </span>
                <Input
                  id="wf-desc"
                  className="w-full"
                  value={description}
                  maxLength={400}
                  placeholder="可保存的团队拆法"
                  onChange={(e) => setDescription(e.target.value)}
                />
              </label>
              <WorkflowSlotsPanel
                definition={definition}
                onChange={setDefinition}
              />
              <WorkflowNodeInspector
                definition={definition}
                selectedId={selectedNode.id}
                onChange={setDefinition}
              />
            </div>
          </aside>
        ) : null}
      </CanvasShell>
      {/* 传已保存的那份：开跑跑的是服务端的 definition，画布上未保存的改动不算。 */}
      <RunWorkflowDialog
        open={runOpen}
        workflowId={workflow.id}
        workflowName={name.trim() || workflow.name}
        definition={workflow.definition}
        source={workflow.source}
        onSlotsSuggested={adoptSuggestedSlots}
        onClose={() => setRunOpen(false)}
      />
    </>
  );
}
