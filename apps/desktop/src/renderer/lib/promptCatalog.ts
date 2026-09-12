import {
  extractCeoIdentity,
  splitWorkerGuideline,
} from "@/lib/splitGuidelineRoles";
import type {
  Capabilities,
  CapabilitySkill,
  CapabilityTool,
} from "@/services/capabilities";
import {
  GLOBAL_PREFERENCES_PATH,
  GLOBAL_PROFILE_PATH,
  parseProjectMemoryFolderId,
} from "@/services/sources/memorySource";

export type PromptCatalogGroupId = "always" | "on_demand";

/** Same order as server `SKILL_GROUP_ORDER` — 能力指引 subtitles. */
export const SKILL_GROUP_ORDER = [
  "编排",
  "工作区",
  "交付",
  "产品",
  "工具",
] as const;

export type SkillCatalogGroup = (typeof SKILL_GROUP_ORDER)[number];

export type PromptCatalogItem =
  | {
      id: "shared";
      kind: "shared";
      group: "factory";
      label: string;
      depth: 0;
      text: string;
    }
  | {
      id: "identity";
      kind: "identity";
      group: "factory";
      label: string;
      depth: 0;
      ceoIdentity: string;
      nestedIdentity: string;
      leafIdentity: string;
    }
  | {
      id: string;
      kind: "skill";
      group: "factory";
      label: string;
      depth: 0;
      tocGroup: string;
      skill: CapabilitySkill;
      parentId: string | null;
    }
  | {
      id: string;
      kind: "mine";
      group: "mine";
      label: string;
      depth: 0;
      mineId: string;
      description: string;
      content: string;
      version: string;
      applyMode: "always" | "on_demand";
      aiMaintained: boolean;
      memoryKind: "preferences" | "profile" | null;
      listable: boolean;
      disputed: boolean;
      /** Chars this entry contributes to the always pool; null when not always. */
      alwaysChars: number | null;
      parentId: string | null;
    }
  | {
      id: string;
      kind: "tool";
      group: "factory";
      label: string;
      depth: 0;
      tool: CapabilityTool;
      parentId: null;
    };

export interface PromptCatalogGroup {
  id: PromptCatalogGroupId;
  label: string;
  testId?: string;
  items: PromptCatalogItem[];
}

export const DEFAULT_PROMPT_CATALOG_ID = "identity";
export const OVERVIEW_CATALOG_ID = "overview";
export const FOLDER_KEY_OFFICIAL = "official";
export const FOLDER_KEY_TOOLS = "tools";
export const FOLDER_KEY_CONNECTORS = "connectors";

/** Fallback 夹 for on-demand files that are not in another 夹. */
export const OTHER_FOLDER_NAME = "其他";
export const OTHER_FOLDER_ID = "virtual:其他";

export function skillCatalogId(name: string): string {
  return `skill:${name}`;
}

export function toolCatalogId(name: string): string {
  return `tool:${name}`;
}

const TOOL_FACE_ORDER = [
  "file",
  "folder",
  "search",
  "web",
  "execution",
  "host_browser",
  "board",
  "orchestration",
] as const;

function toolFaceRank(face: string): number {
  const i = TOOL_FACE_ORDER.indexOf(face as (typeof TOOL_FACE_ORDER)[number]);
  return i === -1 ? TOOL_FACE_ORDER.length : i;
}

function sortTools(tools: CapabilityTool[]): CapabilityTool[] {
  return [...tools].sort((a, b) => {
    const rank = toolFaceRank(a.face) - toolFaceRank(b.face);
    if (rank !== 0) return rank;
    if (a.resident !== b.resident) return a.resident ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

function toToolCatalogItem(
  tool: CapabilityTool,
): Extract<PromptCatalogItem, { kind: "tool" }> {
  return {
    id: toolCatalogId(tool.name),
    kind: "tool",
    group: "factory",
    label: tool.name,
    depth: 0,
    tool,
    parentId: null,
  };
}

function skillGroupRank(group: string | undefined): number {
  const i = SKILL_GROUP_ORDER.indexOf(group as SkillCatalogGroup);
  return i === -1 ? SKILL_GROUP_ORDER.length : i;
}

function sortSkills(skills: CapabilitySkill[]): CapabilitySkill[] {
  return skills
    .map((skill, index) => ({ skill, index }))
    .sort((a, b) => {
      const rank =
        skillGroupRank(a.skill.group) - skillGroupRank(b.skill.group);
      return rank !== 0 ? rank : a.index - b.index;
    })
    .map((row) => row.skill);
}

/** Split the capability payload into 常驻 / 按需 TOC groups. */
export function buildPromptCatalog(data: Capabilities): PromptCatalogGroup[] {
  const standing: PromptCatalogItem[] = [
    {
      id: "shared",
      kind: "shared",
      group: "factory",
      label: "全员共享准则",
      depth: 0,
      text: data.guidelines.shared_base,
    },
    {
      id: "identity",
      kind: "identity",
      group: "factory",
      label: "角色身份",
      depth: 0,
      ceoIdentity: extractCeoIdentity(data.guidelines.ceo_addon),
      nestedIdentity: splitWorkerGuideline(data.guidelines.worker_captain),
      leafIdentity: splitWorkerGuideline(data.guidelines.worker_leaf),
    },
  ];
  const onDemand: PromptCatalogItem[] = sortSkills(data.skills).map(
    (skill) => ({
      id: skillCatalogId(skill.name),
      kind: "skill" as const,
      group: "factory" as const,
      label: skill.summary,
      depth: 0 as const,
      tocGroup: skill.group?.trim() ?? "",
      skill,
      parentId: null,
    }),
  );

  return [
    { id: "always", label: "常驻", items: standing },
    { id: "on_demand", label: "按需", items: onDemand },
  ];
}

export function flattenPromptCatalog(
  groups: PromptCatalogGroup[],
): PromptCatalogItem[] {
  return groups.flatMap((group) => group.items);
}

export function mineCatalogId(id: string): string {
  return `mine:${id}`;
}

export function placeholderCatalogId(kind: "preferences" | "profile"): string {
  return `placeholder:${kind}`;
}

/** Location state when a conversation card / feed row should land on a「我的」entry. */
export interface PromptCatalogLocationState {
  openMineLeaf?: string;
}

const GLOBAL_TOPIC_RE = /^global\/topics\/(.+)$/;

/** Map a global memory-leaf path to the matching「我的」catalog row, or null. */
export function catalogIdForMemoryTarget(
  target: string,
  items: readonly PromptCatalogItem[],
): string | null {
  if (!target) return null;
  if (parseProjectMemoryFolderId(target)) return null;
  const mine = items.filter(
    (item): item is Extract<PromptCatalogItem, { kind: "mine" }> =>
      item.kind === "mine",
  );
  if (target === GLOBAL_PREFERENCES_PATH) {
    return mine.find((row) => row.memoryKind === "preferences")?.id ?? null;
  }
  if (target === GLOBAL_PROFILE_PATH) {
    return mine.find((row) => row.memoryKind === "profile")?.id ?? null;
  }
  const topic = GLOBAL_TOPIC_RE.exec(target);
  const leafName = topic
    ? topic[1]
    : (target.split("/").pop()?.replace(/\.md$/i, "") ?? "");
  if (!leafName) return null;
  return (
    mine.find(
      (row) => row.label === leafName || row.label === displayName(leafName),
    )?.id ?? null
  );
}

export interface OverlayMineRow {
  id: string;
  name: string;
  description: string;
  content: string;
  version: string;
}

export interface AccountScopeEntry {
  id: string;
  name: string;
  description: string;
  applyMode: "always" | "on_demand";
  aiMaintained: boolean;
  disputedAt: string | null;
  alwaysChars: number | null;
  parentId: string | null;
}

export interface MineCatalogRow {
  id: string;
  name: string;
  description: string;
  content: string;
  version: string;
  applyMode: "always" | "on_demand";
  aiMaintained: boolean;
  memoryKind: "preferences" | "profile" | null;
  listable: boolean;
  disputed: boolean;
  alwaysChars: number | null;
  parentId: string | null;
}

const CORE_LEAVES: {
  name: string;
  memoryKind: "preferences" | "profile";
}[] = [
  { name: "偏好.md", memoryKind: "preferences" },
  { name: "画像.md", memoryKind: "profile" },
];

function displayName(name: string): string {
  return name.replace(/\.md$/i, "");
}

function memoryKindFor(
  name: string,
  aiMaintained: boolean,
): "preferences" | "profile" | null {
  if (!aiMaintained) return null;
  if (name === "偏好.md") return "preferences";
  if (name === "画像.md") return "profile";
  return null;
}

function isListableEntry(entry: {
  applyMode: "always" | "on_demand";
  aiMaintained: boolean;
  disputed: boolean;
}): boolean {
  return (
    entry.applyMode === "on_demand" && !entry.aiMaintained && !entry.disputed
  );
}

function mineItemCatalogId(row: MineCatalogRow): string {
  if (!row.id && row.memoryKind) return placeholderCatalogId(row.memoryKind);
  return mineCatalogId(row.id);
}

function toMineCatalogItem(
  row: MineCatalogRow,
): Extract<PromptCatalogItem, { kind: "mine" }> {
  return {
    id: mineItemCatalogId(row),
    kind: "mine",
    group: "mine",
    label: row.name,
    depth: 0,
    mineId: row.id,
    description: row.description,
    content: row.content,
    version: row.version,
    applyMode: row.applyMode,
    aiMaintained: row.aiMaintained,
    memoryKind: row.memoryKind,
    listable: row.listable,
    disputed: row.disputed,
    alwaysChars: row.alwaysChars,
    parentId: row.parentId,
  };
}

/** Account-layer「我的」rows: overlay listable skills + every global entry, cores first. */
export function buildMineCatalogRows(
  overlayMine: OverlayMineRow[],
  scopeEntries: AccountScopeEntry[],
): MineCatalogRow[] {
  const overlayById = new Map(overlayMine.map((row) => [row.id, row]));
  const byId = new Map<string, MineCatalogRow>();

  for (const doc of scopeEntries) {
    const overlay = overlayById.get(doc.id);
    const disputed = doc.disputedAt != null;
    const applyMode = doc.applyMode;
    const aiMaintained = doc.aiMaintained;
    byId.set(doc.id, {
      id: doc.id,
      name: overlay?.name ?? displayName(doc.name),
      description: overlay?.description ?? doc.description,
      content: overlay?.content ?? "",
      version: overlay?.version ?? "",
      applyMode,
      aiMaintained,
      memoryKind: memoryKindFor(doc.name, aiMaintained),
      listable: isListableEntry({ applyMode, aiMaintained, disputed }),
      disputed,
      alwaysChars: doc.alwaysChars,
      parentId: doc.parentId,
    });
  }

  for (const overlay of overlayMine) {
    if (byId.has(overlay.id)) continue;
    byId.set(overlay.id, {
      id: overlay.id,
      name: overlay.name,
      description: overlay.description,
      content: overlay.content,
      version: overlay.version,
      applyMode: "on_demand",
      aiMaintained: false,
      memoryKind: null,
      listable: true,
      disputed: false,
      alwaysChars: null,
      parentId: null,
    });
  }

  const presentNames = new Set(scopeEntries.map((entry) => entry.name));
  const presentKinds = new Set(
    [...byId.values()]
      .map((row) => row.memoryKind)
      .filter((kind): kind is "preferences" | "profile" => kind != null),
  );
  const placeholders: MineCatalogRow[] = CORE_LEAVES.filter(
    (leaf) =>
      !presentNames.has(leaf.name) && !presentKinds.has(leaf.memoryKind),
  ).map((leaf) => ({
    id: "",
    name: displayName(leaf.name),
    description: "",
    content: "",
    version: "",
    applyMode: "always" as const,
    aiMaintained: true,
    memoryKind: leaf.memoryKind,
    listable: false,
    disputed: false,
    alwaysChars: null,
    parentId: null,
  }));

  const rest = [...byId.values()];
  const coreOrder = ["preferences", "profile"] as const;
  const cores = [...placeholders, ...rest.filter((row) => row.memoryKind)].sort(
    (a, b) => {
      const ka = a.memoryKind ?? "";
      const kb = b.memoryKind ?? "";
      return (
        (coreOrder as readonly string[]).indexOf(ka) -
        (coreOrder as readonly string[]).indexOf(kb)
      );
    },
  );
  const others = rest
    .filter((row) => !row.memoryKind)
    .sort((a, b) => a.name.localeCompare(b.name, "zh"));
  return [...cores, ...others];
}

export type PromptRailFolderSource = "system" | "user" | "other";

export interface PromptRailFolder {
  id: string;
  name: string;
  source: PromptRailFolderSource;
  documentId: string | null;
  items: PromptCatalogItem[];
}

export interface PromptRail {
  /** 全员准则 / 角色身份 — product constitution, read-only. */
  constitution: PromptCatalogItem[];
  /** 偏好 / 画像 — memory cores locked in 常驻, not a third region. */
  memory: PromptCatalogItem[];
  /** User-written always files; dragging to 按需 turns them on-demand. */
  alwaysMine: PromptCatalogItem[];
  folders: PromptRailFolder[];
  official: PromptCatalogItem[];
  /** Factory tools — one inventory, not split across 常驻 / 按需. */
  tools: Extract<PromptCatalogItem, { kind: "tool" }>[];
}

export function promptRailAlways(rail: PromptRail): PromptCatalogItem[] {
  return [...rail.constitution, ...rail.memory, ...rail.alwaysMine];
}

/** Dropping on the 按需 zone (not a named 夹) lands in 其他. */
export function onDemandDropFolder(rail: PromptRail): PromptRailFolder {
  return (
    rail.folders.find((folder) => folder.source === "other") ?? {
      id: OTHER_FOLDER_ID,
      name: OTHER_FOLDER_NAME,
      source: "other",
      documentId: null,
      items: [],
    }
  );
}

export function flattenPromptRail(rail: PromptRail): PromptCatalogItem[] {
  return [
    ...promptRailAlways(rail),
    ...rail.folders.flatMap((folder) => folder.items),
    ...rail.official,
    ...rail.tools,
  ];
}

function bucketItems(map: Map<string, PromptCatalogItem[]>, key: string) {
  const items = map.get(key);
  if (items) return items;
  const next: PromptCatalogItem[] = [];
  map.set(key, next);
  return next;
}

/** 常驻 = constitution + memory cores + user always; 用户夹 / 官方 HOW = 按需; 出厂工具另成一份图鉴. 概览行名由 UI 写，不拆字段. */
export function buildPromptRail(
  data: Capabilities,
  mine: MineCatalogRow[],
  folders: { id: string; name: string }[],
  rulesDirId: string | null,
): PromptRail {
  const groups = buildPromptCatalog(data);
  const standing = groups.find((group) => group.id === "always")?.items ?? [];
  const skills = groups.find((group) => group.id === "on_demand")?.items ?? [];
  const tools = sortTools(data.tools).map(toToolCatalogItem);
  const visible = mine;
  const cores = visible.filter((row) => row.memoryKind).map(toMineCatalogItem);
  const alwaysMine = visible
    .filter((row) => !row.memoryKind && row.applyMode === "always")
    .map(toMineCatalogItem);
  const onDemandMine = visible.filter(
    (row) => !row.memoryKind && row.applyMode !== "always",
  );

  const official: PromptCatalogItem[] = skills
    .filter(
      (item): item is Extract<PromptCatalogItem, { kind: "skill" }> =>
        item.kind === "skill",
    )
    .map((skill) => ({ ...skill, parentId: null }));

  const folderById = new Map(folders.map((folder) => [folder.id, folder]));
  const folderByName = new Map(folders.map((folder) => [folder.name, folder]));
  const buckets = new Map<string, PromptCatalogItem[]>();

  for (const row of onDemandMine) {
    const item = toMineCatalogItem(row);
    const parent =
      row.parentId && row.parentId !== rulesDirId
        ? folderById.get(row.parentId)
        : undefined;
    if (parent) bucketItems(buckets, `id:${parent.id}`).push(item);
    else bucketItems(buckets, `name:${OTHER_FOLDER_NAME}`).push(item);
  }

  const result: PromptRailFolder[] = [];
  const userFolders = folders
    .filter((folder) => folder.name !== OTHER_FOLDER_NAME)
    .sort((a, b) => a.name.localeCompare(b.name, "zh"));
  for (const folder of userFolders) {
    result.push({
      id: `folder:${folder.id}`,
      name: folder.name,
      source: "user",
      documentId: folder.id,
      items: buckets.get(`id:${folder.id}`) ?? [],
    });
    buckets.delete(`id:${folder.id}`);
  }

  const otherDoc = folderByName.get(OTHER_FOLDER_NAME);
  const otherItems = [
    ...(otherDoc ? (buckets.get(`id:${otherDoc.id}`) ?? []) : []),
    ...(buckets.get(`name:${OTHER_FOLDER_NAME}`) ?? []),
  ];
  if (otherDoc) buckets.delete(`id:${otherDoc.id}`);
  buckets.delete(`name:${OTHER_FOLDER_NAME}`);
  if (otherItems.length > 0) {
    result.push({
      id: otherDoc ? `folder:${otherDoc.id}` : OTHER_FOLDER_ID,
      name: OTHER_FOLDER_NAME,
      source: "other",
      documentId: otherDoc?.id ?? null,
      items: otherItems,
    });
  }

  return {
    constitution: standing,
    memory: cores,
    alwaysMine,
    folders: result,
    official,
    tools,
  };
}
