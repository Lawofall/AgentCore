import type { ContextBlockWire } from "@/types/events";

/** Context channel → 中文 label + one-line hint. Shared by the briefing-reader TOC. */
export const CONTEXT_CHANNEL_META: Record<
  string,
  { label: string; hint: string }
> = {
  system: { label: "系统提示", hint: "本回合实际遵循的系统指令" },
  history: { label: "对话历史", hint: "本回合之前的往来" },
  request: { label: "原始请求", hint: "老板交给整个团队的目标" },
  team_position: { label: "团队位置", hint: "队友与产出去向" },
  dependency: { label: "前置结果", hint: "上游队友交付的产物" },
  workspace: { label: "工作区", hint: "共享工作区可读文件" },
  task: { label: "你的任务", hint: "分派给本 Agent 的具体活" },
  deliverable: { label: "交付物规格", hint: "本节点落点与结构约束" },
  team_brief: { label: "团队共识", hint: "本回合主协调为全员设定的共识" },
  gate_notes: {
    label: "把关要点",
    hint: "用户已放行的主 Agent 注意事项（非否决）",
  },
  steer: { label: "中途指示", hint: "执行中追加的操舵" },
  team_result: { label: "队员回传", hint: "委派的队员交回 CEO 的产物" },
  round_focus: { label: "本轮焦点", hint: "这一轮辩论聚焦的争议点" },
  opponent: { label: "对方论点", hint: "对方上一轮的发言（供针对性回应）" },
  challenge: { label: "被驳命门", hint: "上一轮裁判记录你被反驳的点" },
  interjection: { label: "用户追问", hint: "用户本轮要求正面回应的问题" },
  cross_exam: { label: "质询", hint: "本轮定向质询：你被追问的问题" },
  closing: { label: "结辩", hint: "收场结辩：归纳本方胜局、不添新论据" },
  continuation: {
    label: "接续指令",
    hint: "带着现场接着干的新指令（改稿或新任务）",
  },
  witness_exam: { label: "证人质询", hint: "本轮证人席作答" },
  attack: { label: "进攻", hint: "本轮进攻陈词" },
  defense: { label: "防守", hint: "本轮防守陈词" },
  rebuttal: { label: "反驳", hint: "本轮反驳" },
  thread: { label: "线索", hint: "本轮圆桌线索" },
  crux: { label: "争点", hint: "本轮争点" },
};

export const FIDELITY_META: Record<string, string> = {
  pointer: "递指针",
  summarize: "摘要",
  pass_through: "全文",
};

export type CatalogGroupId =
  | "turn"
  | "history"
  | "material"
  | "environment"
  | "standing"
  | "other";

export interface CatalogItem {
  id: string;
  group: CatalogGroupId;
  channel: string;
  label: string;
  body: string;
  chars: number;
  truncated: boolean;
  source_role: string;
  source_run_id: string;
  fidelity: string;
  files: string[];
}

export interface CatalogGroup {
  id: CatalogGroupId;
  label: string;
  items: CatalogItem[];
}

const GROUP_META: { id: CatalogGroupId; label: string }[] = [
  { id: "turn", label: "本回合" },
  { id: "history", label: "此前对话" },
  { id: "material", label: "材料" },
  { id: "environment", label: "环境" },
  { id: "standing", label: "常驻指令" },
  { id: "other", label: "其他" },
];

const TURN_CHANNELS = new Set([
  "request",
  "task",
  "continuation",
  "steer",
  "round_focus",
  "challenge",
  "interjection",
  "cross_exam",
  "closing",
  "witness_exam",
  "attack",
  "defense",
  "rebuttal",
  "thread",
  "crux",
]);

const MATERIAL_CHANNELS = new Set(["dependency", "opponent", "team_result"]);

const ENVIRONMENT_CHANNELS = new Set([
  "workspace",
  "team_position",
  "team_brief",
  "deliverable",
  "gate_notes",
]);

function groupForChannel(channel: string): CatalogGroupId {
  if (TURN_CHANNELS.has(channel)) return "turn";
  if (channel === "history") return "history";
  if (MATERIAL_CHANNELS.has(channel)) return "material";
  if (ENVIRONMENT_CHANNELS.has(channel)) return "environment";
  if (channel === "system") return "standing";
  return "other";
}

function channelLabel(channel: string): string {
  return CONTEXT_CHANNEL_META[channel]?.label ?? channel;
}

function itemLabel(block: ContextBlockWire): string {
  const base = channelLabel(block.channel);
  if (
    MATERIAL_CHANNELS.has(block.channel) &&
    block.source_role.trim().length > 0
  ) {
    return `${base} · ${block.source_role}`;
  }
  return base;
}

function fromBlock(
  block: ContextBlockWire,
  id: string,
  overrides: Partial<CatalogItem> = {},
): CatalogItem {
  return {
    id,
    group: groupForChannel(block.channel),
    channel: block.channel,
    label: itemLabel(block),
    body: block.body,
    chars: block.chars,
    truncated: block.truncated,
    source_role: block.source_role,
    source_run_id: block.source_run_id,
    fidelity: block.fidelity,
    files: block.files,
    ...overrides,
  };
}

/**
 * Project `run_context` blocks into TOC groups. Does not invent channels or
 * reorder the wire list inside a group — empty groups are omitted.
 */
export function buildReceivedContextCatalog(
  blocks: readonly ContextBlockWire[],
  opts: { includeSystem: boolean },
): CatalogGroup[] {
  const buckets: Record<CatalogGroupId, CatalogItem[]> = {
    turn: [],
    history: [],
    material: [],
    environment: [],
    standing: [],
    other: [],
  };

  blocks.forEach((block, index) => {
    if (block.channel === "system") {
      if (!opts.includeSystem) return;
      buckets.standing.push(
        fromBlock(block, `b${index}`, { label: "常驻指令" }),
      );
      return;
    }

    const item = fromBlock(block, `b${index}`);
    buckets[item.group].push(item);
  });

  return GROUP_META.filter((g) => buckets[g.id].length > 0).map((g) => ({
    id: g.id,
    label: g.label,
    items: buckets[g.id],
  }));
}

export function flattenCatalog(groups: readonly CatalogGroup[]): CatalogItem[] {
  return groups.flatMap((g) => g.items);
}

/** Prefer `channel=request`; with `preferMaterial`, first 材料 row wins (worker dock). */
export function defaultCatalogItemId(
  groups: readonly CatalogGroup[],
  opts?: { preferMaterial?: boolean },
): string | null {
  const items = flattenCatalog(groups);
  if (opts?.preferMaterial) {
    const material = items.find((i) => i.group === "material");
    if (material) return material.id;
  }
  return items.find((i) => i.channel === "request")?.id ?? items[0]?.id ?? null;
}
