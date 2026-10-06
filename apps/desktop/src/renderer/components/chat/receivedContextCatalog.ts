import type { PromptSection } from "@/lib/parsePromptDocument";
import { parsePromptDocument } from "@/lib/parsePromptDocument";
import type { ContextBlockWire, ProcessStep } from "@/types/events";

/** Context channel → 中文 label + one-line hint. Shared by the briefing-reader TOC. */
export const CONTEXT_CHANNEL_META: Record<
  string,
  { label: string; hint: string }
> = {
  system: { label: "系统提示", hint: "本回合实际遵循的系统指令" },
  tools: { label: "本回合工具", hint: "开场实际发给模型的工具表" },
  history: { label: "对话历史", hint: "本回合之前的往来" },
  request: { label: "原始请求", hint: "老板交给整个团队的目标" },
  team_position: { label: "团队位置", hint: "队友与产出去向" },
  dependency: { label: "前置", hint: "上游队友交付的产物" },
  workspace: { label: "工作区", hint: "共享工作区可读文件" },
  task: { label: "你的任务", hint: "分派给本 Agent 的具体活" },
  deliverable: { label: "交付物规格", hint: "本节点落点与结构约束" },
  team_brief: { label: "团队共识", hint: "本回合主协调为全员设定的共识" },
  gate_notes: {
    label: "把关要点",
    hint: "把关时写入的注意事项",
  },
  steer: { label: "中途指示", hint: "执行中追加的操舵" },
  team_result: { label: "回传", hint: "委派的队员交回 CEO 的产物" },
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
  thread: { label: "线索", hint: "本轮线索" },
  crux: { label: "争点", hint: "本轮争点" },
};

export type CatalogGroupId =
  | "turn"
  | "history"
  | "material"
  | "environment"
  | "standing"
  | "later"
  | "other";

export interface CatalogItem {
  id: string;
  group: CatalogGroupId;
  channel: string;
  /** Prompt tag when this row is a slice of `channel=system`; otherwise null. */
  tag: string | null;
  /** Honest empty row: this turn's system text has no `<设定>`. */
  absent: boolean;
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
  { id: "standing", label: "系统" },
  { id: "turn", label: "本回合" },
  { id: "later", label: "后来查阅" },
  { id: "history", label: "此前对话" },
  { id: "material", label: "材料" },
  { id: "environment", label: "环境" },
  { id: "other", label: "其他" },
];

/** Layers lifted out of the system blob. Path rows and `<运行时>` appear only
 * when those tags are already in the system text. Factory remainder stays one row. */
const PINNED_SYSTEM_TAGS = [
  "设定",
  "路径约定",
  "路径约定全文",
  "按需目录",
  "工作区",
  "运行时",
] as const;

const CONSULT_TOOLS = new Set([
  "consult",
  "consult_skill",
  "consult_memory",
  "consult_rule",
]);

const TURN_CHANNELS = new Set([
  "tools",
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
  if (channel === "consult_receipt") return "later";
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
    tag: null,
    absent: false,
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

function isPinnedTag(
  tag: string | null,
): tag is (typeof PINNED_SYSTEM_TAGS)[number] {
  return tag != null && (PINNED_SYSTEM_TAGS as readonly string[]).includes(tag);
}

function taggedText(section: PromptSection): string {
  if (section.tag) {
    return `<${section.tag}>\n${section.body}\n</${section.tag}>`;
  }
  return section.body;
}

function joinSections(sections: readonly PromptSection[]): string {
  return sections.map(taggedText).filter(Boolean).join("\n\n");
}

function absentSettingItem(
  block: ContextBlockWire,
  blockIndex: number,
): CatalogItem {
  return fromBlock(block, `b${blockIndex}:设定:absent`, {
    group: "standing",
    tag: "设定",
    absent: true,
    label: "设定",
    body: "本回合未注入常驻规则。",
    chars: 0,
    truncated: false,
    files: [],
  });
}

function pinnedSlice(
  block: ContextBlockWire,
  blockIndex: number,
  section: PromptSection,
  sectionIndex: number,
): CatalogItem {
  const tag = section.tag ?? "untagged";
  return fromBlock(block, `b${blockIndex}:${tag}:${sectionIndex}`, {
    group: "standing",
    tag: section.tag,
    label: section.title || tag,
    body: section.body,
    chars: section.body.length,
    truncated: false,
    files: [],
  });
}

function factoryItem(
  block: ContextBlockWire,
  blockIndex: number,
  rest: readonly PromptSection[],
): CatalogItem {
  const body = joinSections(rest);
  return fromBlock(block, `b${blockIndex}:factory`, {
    group: "standing",
    tag: null,
    label: "出厂指令",
    body,
    chars: body.length,
    truncated: false,
    files: [],
  });
}

/**
 * Same `channel=system` body the model ate, indexed by existing tags.
 * Pinned layers become their own TOC rows; remaining constitution stays one
 * factory row. Missing `<设定>` is an honest empty row, not a file-page backfill.
 * `<路径约定>`, `<路径约定全文>`, and `<运行时>` are rows only when present.
 * `<运行时>` sits beside `<工作区>`: both are per-turn envelope facts.
 */
function splitSystemBlock(
  block: ContextBlockWire,
  blockIndex: number,
): CatalogItem[] {
  const sections = parsePromptDocument(block.body);
  const items: CatalogItem[] = [];
  if (!sections.some((section) => section.tag === "设定")) {
    items.push(absentSettingItem(block, blockIndex));
  }

  const pinned: Record<(typeof PINNED_SYSTEM_TAGS)[number], PromptSection[]> = {
    设定: [],
    路径约定: [],
    路径约定全文: [],
    按需目录: [],
    工作区: [],
    运行时: [],
  };
  const rest: PromptSection[] = [];
  for (const section of sections) {
    if (isPinnedTag(section.tag)) pinned[section.tag].push(section);
    else rest.push(section);
  }

  PINNED_SYSTEM_TAGS.forEach((tag, tagIndex) => {
    pinned[tag].forEach((section, sliceIndex) => {
      items.push(
        pinnedSlice(block, blockIndex, section, tagIndex * 100 + sliceIndex),
      );
    });
  });
  if (rest.length > 0) items.push(factoryItem(block, blockIndex, rest));
  return items;
}

function displayField(display: unknown, key: string): string | null {
  if (!display || typeof display !== "object") return null;
  const value = (display as Record<string, unknown>)[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function consultName(step: Extract<ProcessStep, { kind: "tool" }>): string {
  const display = step.display;
  return (
    displayField(display, "name") ??
    displayField(display, "skill_name") ??
    displayField(display, "topic") ??
    displayField(display, "rule") ??
    (typeof step.arguments.name === "string" && step.arguments.name.trim()
      ? step.arguments.name.trim()
      : step.tool_name)
  );
}

function laterConsultItems(process: readonly ProcessStep[]): CatalogItem[] {
  const items: CatalogItem[] = [];
  for (const step of process) {
    if (step.kind !== "tool") continue;
    if (!CONSULT_TOOLS.has(step.tool_name)) continue;
    if (step.status === "running") continue;
    const body = (step.result ?? "").trim();
    if (!body) continue;
    const name = consultName(step);
    items.push({
      id: `later:consult:${step.id}`,
      group: "later",
      channel: "consult_receipt",
      tag: null,
      absent: false,
      label: `查阅 · ${name}`,
      body,
      chars: body.length,
      truncated: false,
      source_role: "",
      source_run_id: "",
      fidelity: "",
      files: [],
    });
  }
  return items;
}

/**
 * Project `run_context` blocks into TOC groups. System stays one wire
 * channel — slices are a reader index, not a second assembly. Consult
 * receipts are indexed from the same process steps the timeline already
 * shows. Empty groups are omitted.
 */
export function buildReceivedContextCatalog(
  blocks: readonly ContextBlockWire[],
  opts: { includeSystem: boolean; process?: readonly ProcessStep[] },
): CatalogGroup[] {
  const buckets: Record<CatalogGroupId, CatalogItem[]> = {
    turn: [],
    history: [],
    material: [],
    environment: [],
    standing: [],
    later: [],
    other: [],
  };

  blocks.forEach((block, index) => {
    if (block.channel === "system") {
      if (!opts.includeSystem) return;
      for (const item of splitSystemBlock(block, index)) {
        buckets.standing.push(item);
      }
      return;
    }

    const item = fromBlock(block, `b${index}`);
    buckets[item.group].push(item);
  });

  if (opts.process) {
    for (const item of laterConsultItems(opts.process)) {
      buckets.later.push(item);
    }
  }

  return GROUP_META.filter((g) => buckets[g.id].length > 0).map((g) => ({
    id: g.id,
    label: g.label,
    items: buckets[g.id],
  }));
}

export function flattenCatalog(groups: readonly CatalogGroup[]): CatalogItem[] {
  return groups.flatMap((g) => g.items);
}

/** TOC id for the opening-projection row. Not a catalog slice. */
export const RECEIVED_CONTEXT_ALL_ID = "all";

export interface OpeningSection {
  heading: string;
  channel: string;
  body: string;
  truncated: boolean;
  fidelity: string;
  files: string[];
}

/**
 * Opening projection in wire order: the blocks the model was fed at the start
 * of the turn. System stays one body (catalog slices are an index). Consult
 * receipts are not blocks and stay out. Narrow layouts drop `channel=system`.
 */
export function openingContextView(
  blocks: readonly ContextBlockWire[],
  opts: { includeSystem: boolean },
): { sections: OpeningSection[]; chars: number } {
  const included = opts.includeSystem
    ? blocks
    : blocks.filter((block) => block.channel !== "system");
  let chars = 0;
  const sections = included.map((block) => {
    chars += block.chars;
    return {
      heading: block.heading,
      channel: block.channel,
      body: block.body,
      truncated: block.truncated,
      fidelity: block.fidelity,
      files: block.files,
    };
  });
  return { sections, chars };
}

/** CEO: injected 设定 → 原始请求 → 本回合工具. Worker dock: first 材料 row wins. */
export function defaultCatalogItemId(
  groups: readonly CatalogGroup[],
  opts?: { preferMaterial?: boolean },
): string | null {
  const items = flattenCatalog(groups);
  if (opts?.preferMaterial) {
    const material = items.find((i) => i.group === "material");
    if (material) return material.id;
  }
  return (
    items.find((i) => i.tag === "设定" && !i.absent)?.id ??
    items.find((i) => i.channel === "request")?.id ??
    items.find((i) => i.channel === "tools")?.id ??
    items[0]?.id ??
    null
  );
}
