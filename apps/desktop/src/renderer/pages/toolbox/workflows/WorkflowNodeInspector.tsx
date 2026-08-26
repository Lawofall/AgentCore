import { Input, Select, Textarea } from "@/components/ui";
import {
  type WorkflowDefNode,
  type WorkflowDefinition,
  type WorkflowDeliverable,
  renderSlotText,
  slotKeysInText,
  slotPlaceholder,
  workflowSlotDefaults,
  workflowSlots,
} from "@/services/workflowDefinition";

const DELIVERABLE_FORMS = [
  { value: "prose", label: "纯文字" },
  { value: "files", label: "文档" },
  { value: "workspace", label: "改工程" },
] as const;

type DeliverableForm = (typeof DELIVERABLE_FORMS)[number]["value"];

function isDeliverableForm(value: string): value is DeliverableForm {
  return value === "prose" || value === "files" || value === "workspace";
}

/** 缺省或旧自由文都按文档档展示，不在渲染时改写 definition。 */
function shownDeliverableForm(form: string | undefined): DeliverableForm {
  return form !== undefined && isDeliverableForm(form) ? form : "files";
}

/**
 * 只改 `form`，其余交付契约（artifacts / required_sections / strict …）逐字保留：
 * 画布把整份 definition 原样 PATCH 回去，这里换成 `{ form }` 就等于用户改一次
 * 交付形式便抹掉契约。
 */
function withDeliverableForm(
  current: WorkflowDeliverable | undefined,
  form: DeliverableForm,
): WorkflowDeliverable {
  const next: WorkflowDeliverable = {};
  for (const [key, value] of Object.entries(current ?? {})) {
    if (key !== "form") next[key] = value;
  }
  next.form = form;
  return next;
}

/**
 * 任务文本里的 `{{key}}` 解释给用户看：列出引用到的参数 + 按默认值的成文预览。
 * 未声明的 key 如实标出来（它跑起来不会被替换），但不拦保存。
 */
function TaskSlotHints({
  definition,
  task,
}: {
  definition: WorkflowDefinition;
  task: string;
}) {
  const keys = slotKeysInText(task);
  if (keys.length === 0) return null;
  const labels = new Map(
    workflowSlots(definition).map((s) => [s.key, s.label]),
  );
  return (
    <div className="space-y-1.5 rounded-lg border border-border p-2.5">
      <p className="text-xs text-muted-foreground">
        这段里的 {slotPlaceholder("参数")} 会在跑一次时换成当轮的值：
      </p>
      <div className="flex flex-wrap gap-1.5">
        {keys.map((key) => (
          <span
            key={key}
            className="rounded-lg bg-muted px-1.5 py-0.5 text-xs text-foreground"
          >
            <code className="font-mono">{slotPlaceholder(key)}</code>
            {labels.has(key) ? (
              <span className="ml-1 text-muted-foreground">
                {labels.get(key)}
              </span>
            ) : (
              <span className="ml-1 text-warning">未声明</span>
            )}
          </span>
        ))}
      </div>
      <p className="line-clamp-3 text-xs text-muted-foreground">
        按默认值：{renderSlotText(task, workflowSlotDefaults(definition))}
      </p>
    </div>
  );
}

export function WorkflowNodeInspector({
  definition,
  selectedId,
  onChange,
}: {
  definition: WorkflowDefinition;
  selectedId: string | null;
  onChange: (next: WorkflowDefinition) => void;
}) {
  const node = definition.nodes.find((n) => n.id === selectedId) ?? null;

  if (!node) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        选中画布上的节点以编辑属性。
      </div>
    );
  }

  const patch = (next: WorkflowDefNode) => {
    onChange({
      ...definition,
      nodes: definition.nodes.map((n) => (n.id === next.id ? next : n)),
    });
  };

  if (node.kind === "human_gate") {
    return (
      <div className="space-y-4 p-4">
        <div>
          <p className="text-sm font-medium text-foreground">等人关卡</p>
          <p className="mt-1 text-xs text-muted-foreground">
            前驱步骤完成后暂停，等人确认再继续。
          </p>
        </div>
        <label className="block" htmlFor="wf-gate-label">
          <span className="mb-1 block text-xs text-muted-foreground">标签</span>
          <Input
            id="wf-gate-label"
            className="w-full"
            value={node.label}
            maxLength={80}
            placeholder="例如：审初稿"
            onChange={(e) => patch({ ...node, label: e.target.value })}
          />
        </label>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4">
      <div>
        <p className="text-sm font-medium text-foreground">队员步骤</p>
        <p className="mt-1 text-xs text-muted-foreground">
          展开为一条委派任务；依赖由连线决定。
        </p>
      </div>
      <label className="block" htmlFor="wf-role">
        <span className="mb-1 block text-xs text-muted-foreground">角色</span>
        <Input
          id="wf-role"
          className="w-full"
          value={node.role}
          maxLength={80}
          placeholder="例如：调研员"
          onChange={(e) => patch({ ...node, role: e.target.value })}
        />
      </label>
      <label className="block" htmlFor="wf-task">
        <span className="mb-1 block text-xs text-muted-foreground">
          任务说明
        </span>
        <Textarea
          id="wf-task"
          className="w-full text-sm"
          rows={5}
          value={node.task}
          maxLength={2000}
          placeholder="这步要完成什么？"
          onChange={(e) => patch({ ...node, task: e.target.value })}
        />
      </label>
      <TaskSlotHints definition={definition} task={node.task} />
      <label className="block" htmlFor="wf-deliverable">
        <span className="mb-1 block text-xs text-muted-foreground">
          交付形式
        </span>
        <Select
          id="wf-deliverable"
          value={shownDeliverableForm(node.deliverable?.form)}
          onChange={(e) => {
            const form = e.target.value;
            if (!isDeliverableForm(form)) return;
            patch({
              ...node,
              deliverable: withDeliverableForm(node.deliverable, form),
            });
          }}
        >
          {DELIVERABLE_FORMS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </Select>
      </label>
    </div>
  );
}
