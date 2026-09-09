/**
 * User workflow definition (定案 §10.2) — canvas JSON shape + first-wave validation.
 * Kept separate from the REST client so unit tests need no API mocks.
 */

/** Max expandable agent steps — align with global delegate cap (20). */
export const WORKFLOW_MAX_AGENT_STEPS = 20;

export type WorkflowNodeKind = "agent_step" | "human_gate";

/**
 * 交付契约快照 — 服务端 `Deliverable` 的整体承载。
 *
 * 画布不把 `form` 当一等字段（旧三档已撤，解析时丢掉该键、不做兼容翻译）。
 * 同一份 definition 会被 PATCH 原样写回，所以 `artifacts` / `required_sections` /
 * `strict` / `citation_mode` 等其余字段必须逐字保留：解析时丢字段 = 用户在画布上
 * 点一次保存就抹掉交付契约。故此处不枚举字段，未知键一律透传。
 */
export interface WorkflowDeliverable {
  [key: string]: unknown;
}

export interface WorkflowAgentStepNode {
  id: string;
  kind: "agent_step";
  role: string;
  task: string;
  deliverable?: WorkflowDeliverable;
}

export interface WorkflowHumanGateNode {
  id: string;
  kind: "human_gate";
  label: string;
}

export type WorkflowDefNode = WorkflowAgentStepNode | WorkflowHumanGateNode;

export interface WorkflowDefEdge {
  from: string;
  to: string;
}

/**
 * 可换值的槽位 — definition 顶层声明，节点 task 里用 `{{key}}` 引用。
 *
 * `default` 是固化那一轮的原值：跑一次不改任何槽位 = 原样重跑。同 `deliverable`，
 * 未知键逐字透传（画布保存会把整份 definition 原样 PATCH 回去）。
 */
export interface WorkflowSlot {
  key: string;
  label: string;
  default: string;
  [key: string]: unknown;
}

/**
 * 画布 JSON。`slots` 之外的顶层字段同样逐字保留：这份对象会被原样 PATCH 回去，
 * 解析时白名单过滤 = 用户点一次保存就把后端写的东西抹了（交付契约已踩过一次）。
 */
export interface WorkflowDefinition {
  nodes: WorkflowDefNode[];
  edges: WorkflowDefEdge[];
  slots?: WorkflowSlot[];
  [key: string]: unknown;
}

export interface WorkflowDefinitionIssue {
  code:
    | "empty_role"
    | "empty_task"
    | "empty_gate_label"
    | "unknown_edge_endpoint"
    | "cycle"
    | "too_many_steps"
    | "duplicate_id"
    | "invalid_kind"
    | "gate_to_gate"
    | "gate_without_agent_pred";
  message: string;
  nodeId?: string;
}

export function emptyWorkflowDefinition(): WorkflowDefinition {
  return { nodes: [], edges: [] };
}

export function newNodeId(prefix = "n"): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

export function createAgentStepNode(
  partial?: Partial<Omit<WorkflowAgentStepNode, "kind">>,
): WorkflowAgentStepNode {
  return {
    id: partial?.id ?? newNodeId("step"),
    kind: "agent_step",
    role: partial?.role ?? "",
    task: partial?.task ?? "",
    deliverable: partial?.deliverable,
  };
}

export function createHumanGateNode(
  partial?: Partial<Omit<WorkflowHumanGateNode, "kind">>,
): WorkflowHumanGateNode {
  return {
    id: partial?.id ?? newNodeId("gate"),
    kind: "human_gate",
    label: partial?.label ?? "等人确认",
  };
}

/**
 * 占位符写法：`{{key}}`，容忍花括号内空白。渲染在服务端，这里只做识别与预览。
 *
 * key 字符集必须与服务端 `workflows/slots.py` 逐字一致：认得比服务端多，画布上就会
 * 把 `{{Topic}}` 画成变量胶囊而跑出来是字面量。
 */
const SLOT_PLACEHOLDER_SOURCE = "\\{\\{\\s*([a-z][a-z0-9_]{0,23})\\s*\\}\\}";

/** 带 `g` 的正则有 `lastIndex` 状态，每次现造一个，别共享。 */
function slotPlaceholderRe(): RegExp {
  return new RegExp(SLOT_PLACEHOLDER_SOURCE, "g");
}

export function slotPlaceholder(key: string): string {
  return `{{${key}}}`;
}

/**
 * 任务文本切片：`slot` 段供 UI 渲染成「一眼看得出是变量」的样子。
 * `start` 是该段在原文里的起点，重复文案也能拿它当稳定 React key。
 */
export type WorkflowTaskSegment =
  | { kind: "text"; text: string; start: number }
  | { kind: "slot"; key: string; raw: string; start: number };

export function splitSlotPlaceholders(text: string): WorkflowTaskSegment[] {
  const re = slotPlaceholderRe();
  const out: WorkflowTaskSegment[] = [];
  let last = 0;
  for (let m = re.exec(text); m !== null; m = re.exec(text)) {
    if (m.index > last) {
      out.push({ kind: "text", text: text.slice(last, m.index), start: last });
    }
    out.push({ kind: "slot", key: m[1] ?? "", raw: m[0], start: m.index });
    last = m.index + m[0].length;
  }
  if (last < text.length) {
    out.push({ kind: "text", text: text.slice(last), start: last });
  }
  return out;
}

/** 文本引用到的槽位 key（按出现顺序去重）。 */
export function slotKeysInText(text: string): string[] {
  const keys: string[] = [];
  for (const seg of splitSlotPlaceholders(text)) {
    if (seg.kind === "slot" && !keys.includes(seg.key)) keys.push(seg.key);
  }
  return keys;
}

/**
 * 预览用替换；没给值的占位符保持原样（真正的渲染在服务端）。
 * 取值走 `Object.hasOwn`：`{{toString}}` 这种 key 用 `in` 会命中原型链上的函数。
 */
export function renderSlotText(
  text: string,
  values: Record<string, string>,
): string {
  return text.replace(slotPlaceholderRe(), (raw, key: string) =>
    Object.hasOwn(values, key) ? values[key] : raw,
  );
}

export function workflowSlots(def: WorkflowDefinition): WorkflowSlot[] {
  return def.slots ?? [];
}

/** key → default 取值表：跑一次的预填与画布预览共用同一份。 */
export function workflowSlotDefaults(
  def: WorkflowDefinition,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const slot of workflowSlots(def)) out[slot.key] = slot.default;
  return out;
}

function hasCycle(ids: string[], edges: WorkflowDefEdge[]): boolean {
  const outs = new Map<string, string[]>();
  for (const id of ids) outs.set(id, []);
  for (const e of edges) {
    outs.get(e.from)?.push(e.to);
  }
  const visiting = new Set<string>();
  const done = new Set<string>();
  const dfs = (id: string): boolean => {
    if (done.has(id)) return false;
    if (visiting.has(id)) return true;
    visiting.add(id);
    for (const next of outs.get(id) ?? []) {
      if (dfs(next)) return true;
    }
    visiting.delete(id);
    done.add(id);
    return false;
  };
  return ids.some((id) => dfs(id));
}

function kindById(def: WorkflowDefinition): Map<string, WorkflowNodeKind> {
  const m = new Map<string, WorkflowNodeKind>();
  for (const n of def.nodes) m.set(n.id, n.kind);
  return m;
}

/** Reachable agent_step ancestors walking back through human_gate chains. */
function agentAncestorsThroughGates(
  gateId: string,
  edges: WorkflowDefEdge[],
  kinds: Map<string, WorkflowNodeKind>,
): string[] {
  const found: string[] = [];
  const seen = new Set<string>();
  const stack = [gateId];
  const visiting = new Set<string>();
  while (stack.length > 0) {
    const nid = stack.pop();
    if (nid === undefined) break;
    if (visiting.has(nid)) continue;
    visiting.add(nid);
    for (const e of edges) {
      if (e.to !== nid) continue;
      const srcKind = kinds.get(e.from);
      if (srcKind === "agent_step") {
        if (!seen.has(e.from)) {
          seen.add(e.from);
          found.push(e.from);
        }
      } else if (srcKind === "human_gate" && !visiting.has(e.from)) {
        stack.push(e.from);
      }
    }
  }
  return found;
}

/**
 * Whether a new canvas connection is allowed (aligns with server edge policy /
 * tasks_to_workflow_definition normal form).
 */
export function isWorkflowConnectionAllowed(
  def: WorkflowDefinition,
  fromId: string,
  toId: string,
): boolean {
  const kinds = kindById(def);
  const srcKind = kinds.get(fromId);
  const dstKind = kinds.get(toId);
  if (!srcKind || !dstKind) return false;
  if (srcKind === "human_gate" && dstKind === "human_gate") return false;
  if (srcKind === "human_gate" && dstKind === "agent_step") {
    return agentAncestorsThroughGates(fromId, def.edges, kinds).length > 0;
  }
  return true;
}

/** Structural validation for save / run. */
export function validateWorkflowDefinition(
  def: WorkflowDefinition,
): WorkflowDefinitionIssue[] {
  const issues: WorkflowDefinitionIssue[] = [];
  const seen = new Set<string>();
  let agentCount = 0;

  for (const node of def.nodes) {
    if (seen.has(node.id)) {
      issues.push({
        code: "duplicate_id",
        message: `重复节点 id：${node.id}`,
        nodeId: node.id,
      });
      continue;
    }
    seen.add(node.id);

    if (node.kind === "agent_step") {
      agentCount += 1;
      if (!node.role.trim()) {
        issues.push({
          code: "empty_role",
          message: "队员步骤须填写角色",
          nodeId: node.id,
        });
      }
      if (!node.task.trim()) {
        issues.push({
          code: "empty_task",
          message: "队员步骤须填写任务说明",
          nodeId: node.id,
        });
      }
    } else if (node.kind === "human_gate") {
      if (!node.label.trim()) {
        issues.push({
          code: "empty_gate_label",
          message: "等人关卡须填写标签",
          nodeId: node.id,
        });
      }
    } else {
      issues.push({
        code: "invalid_kind",
        message: "未知节点类型",
        nodeId: (node as WorkflowDefNode).id,
      });
    }
  }

  if (agentCount > WORKFLOW_MAX_AGENT_STEPS) {
    issues.push({
      code: "too_many_steps",
      message: `队员步骤不得超过 ${WORKFLOW_MAX_AGENT_STEPS} 个`,
    });
  }

  const kinds = kindById(def);
  for (const e of def.edges) {
    if (!seen.has(e.from) || !seen.has(e.to)) {
      issues.push({
        code: "unknown_edge_endpoint",
        message: `连线端点不存在：${e.from} → ${e.to}`,
      });
      continue;
    }
    const srcKind = kinds.get(e.from);
    const dstKind = kinds.get(e.to);
    if (srcKind === "human_gate" && dstKind === "human_gate") {
      issues.push({
        code: "gate_to_gate",
        message: `禁止等人关卡连到等人关卡：${e.from} → ${e.to}`,
        nodeId: e.from,
      });
    } else if (
      srcKind === "human_gate" &&
      dstKind === "agent_step" &&
      agentAncestorsThroughGates(e.from, def.edges, kinds).length === 0
    ) {
      issues.push({
        code: "gate_without_agent_pred",
        message: `等人关卡 ${e.from} 无队员步骤前驱，不能连到 ${e.to}`,
        nodeId: e.from,
      });
    }
  }

  if (hasCycle([...seen], def.edges)) {
    issues.push({ code: "cycle", message: "工作流图不能有环" });
  }

  return issues;
}

/**
 * Normalize a node's `deliverable`, dropping legacy `form` and passing every
 * other key through verbatim (canvas PATCH must not wipe artifacts / strict …).
 */
function parseDeliverable(raw: unknown): WorkflowDeliverable | undefined {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
  const rest = { ...(raw as Record<string, unknown>) };
  const { form: _legacyForm, ...withoutForm } = rest;
  void _legacyForm;
  return withoutForm;
}

/**
 * Normalize one slot, preserving every field verbatim.
 * Only `label` / `default` are normalized (the UI renders them as text); a slot
 * without a usable `key` is dropped — nothing in a task text could reference it.
 */
function parseSlot(raw: unknown): WorkflowSlot | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const {
    key,
    label,
    default: fallback,
    ...rest
  } = raw as Record<string, unknown>;
  const trimmedKey = typeof key === "string" ? key.trim() : "";
  if (!trimmedKey) return null;
  const trimmedLabel = typeof label === "string" ? label.trim() : "";
  return {
    ...rest,
    key: trimmedKey,
    label: trimmedLabel || trimmedKey,
    default: typeof fallback === "string" ? fallback : "",
  };
}

/** Normalize unknown JSON into a definition (drops invalid entries). */
export function parseWorkflowDefinition(raw: unknown): WorkflowDefinition {
  if (!raw || typeof raw !== "object") return emptyWorkflowDefinition();
  // 顶层未知键（后端新加的字段）留在 `rest` 里原样带走，不做白名单。
  const {
    nodes: rawNodes,
    edges: rawEdges,
    slots: rawSlots,
    ...rest
  } = raw as Record<string, unknown>;
  const nodes: WorkflowDefNode[] = [];
  if (Array.isArray(rawNodes)) {
    for (const n of rawNodes) {
      if (!n || typeof n !== "object") continue;
      const row = n as Record<string, unknown>;
      const id = typeof row.id === "string" ? row.id : "";
      if (!id) continue;
      if (row.kind === "agent_step") {
        const deliverable = parseDeliverable(row.deliverable);
        nodes.push({
          id,
          kind: "agent_step",
          role: typeof row.role === "string" ? row.role : "",
          task: typeof row.task === "string" ? row.task : "",
          deliverable,
        });
      } else if (row.kind === "human_gate") {
        nodes.push({
          id,
          kind: "human_gate",
          label: typeof row.label === "string" ? row.label : "",
        });
      }
    }
  }
  const edges: WorkflowDefEdge[] = [];
  if (Array.isArray(rawEdges)) {
    for (const e of rawEdges) {
      if (!e || typeof e !== "object") continue;
      const row = e as Record<string, unknown>;
      if (typeof row.from === "string" && typeof row.to === "string") {
        edges.push({ from: row.from, to: row.to });
      }
    }
  }
  const def: WorkflowDefinition = { ...rest, nodes, edges };
  // 非数组 slots 不符合契约，按未声明处理（留着会让画布拿它当列表渲染）。
  if (Array.isArray(rawSlots)) {
    def.slots = rawSlots
      .map(parseSlot)
      .filter((s): s is WorkflowSlot => s !== null);
  }
  return def;
}
