import { api } from "@/services/api";
import { scheduleAccountRulesMemoryRefresh } from "@/services/refreshAccountRulesMemory";

/** Overlay for 工具箱「AI 提示词」: 我的技能 + 官方槽换用 / 藏起.
 *  Not the deployment 图鉴 (`GET /v1/capabilities`). Optional ``folderId``
 *  is one folder layer; the server merges account → ancestors → this folder.
 */

export type OverlayLayer = "here" | "inherited";

export interface SkillReplacedBy {
  documentId: string;
  name: string;
  description: string;
}

export interface SkillSlot {
  name: string;
  summary: string;
  replacedBy: SkillReplacedBy | null;
  muted: boolean;
  replacedLayer: OverlayLayer | null;
  mutedLayer: OverlayLayer | null;
}

export interface MineSkill {
  id: string;
  name: string;
  description: string;
  content: string;
  version: string;
  occupies: string[];
}

export interface SkillCatalog {
  slots: SkillSlot[];
  mine: MineSkill[];
  folderId: string | null;
  writable: boolean;
}

export const EMPTY_SKILL_CATALOG: SkillCatalog = {
  slots: [],
  mine: [],
  folderId: null,
  writable: true,
};

interface SlotWire {
  name: string;
  summary: string;
  replaced_by: {
    document_id: string;
    name: string;
    description: string;
  } | null;
  muted?: boolean;
  replaced_layer?: OverlayLayer | null;
  muted_layer?: OverlayLayer | null;
}

interface MineWire {
  id: string;
  name: string;
  description: string;
  content: string;
  version: string;
  occupies: string[];
}

interface CatalogWire {
  slots: SlotWire[];
  mine: MineWire[];
  folder_id?: string | null;
  writable?: boolean;
}

function catalogQuery(folderId?: string | null): string {
  return folderId ? `?folder_id=${encodeURIComponent(folderId)}` : "";
}

function toCatalog(w: CatalogWire): SkillCatalog {
  return {
    folderId: w.folder_id ?? null,
    writable: w.writable !== false,
    slots: w.slots.map((slot) => ({
      name: slot.name,
      summary: slot.summary,
      replacedBy: slot.replaced_by
        ? {
            documentId: slot.replaced_by.document_id,
            name: slot.replaced_by.name,
            description: slot.replaced_by.description,
          }
        : null,
      muted: Boolean(slot.muted),
      replacedLayer: slot.replaced_layer ?? null,
      mutedLayer: slot.muted_layer ?? null,
    })),
    mine: w.mine.map((item) => ({
      id: item.id,
      name: item.name,
      description: item.description,
      content: item.content,
      version: item.version,
      occupies: item.occupies,
    })),
  };
}

export function getSkillCatalog(
  folderId?: string | null,
): Promise<SkillCatalog> {
  return api
    .get<CatalogWire>(`/v1/skill-catalog${catalogQuery(folderId)}`)
    .then(toCatalog);
}

export function replaceSkillSlot(
  slot: string,
  documentId: string,
  folderId?: string | null,
): Promise<SkillCatalog> {
  return api
    .put<CatalogWire>(
      `/v1/skill-catalog/replacements/${encodeURIComponent(slot)}${catalogQuery(folderId)}`,
      { document_id: documentId },
    )
    .then(toCatalog)
    .then((catalog) => {
      scheduleAccountRulesMemoryRefresh();
      return catalog;
    });
}

export function restoreSkillSlot(
  slot: string,
  folderId?: string | null,
): Promise<SkillCatalog> {
  return api
    .delete<CatalogWire>(
      `/v1/skill-catalog/replacements/${encodeURIComponent(slot)}${catalogQuery(folderId)}`,
    )
    .then(toCatalog)
    .then((catalog) => {
      scheduleAccountRulesMemoryRefresh();
      return catalog;
    });
}

export function muteSkillSlot(
  slot: string,
  folderId?: string | null,
): Promise<SkillCatalog> {
  return api
    .put<CatalogWire>(
      `/v1/skill-catalog/mutes/${encodeURIComponent(slot)}${catalogQuery(folderId)}`,
    )
    .then(toCatalog)
    .then((catalog) => {
      scheduleAccountRulesMemoryRefresh();
      return catalog;
    });
}

export function unmuteSkillSlot(
  slot: string,
  folderId?: string | null,
): Promise<SkillCatalog> {
  return api
    .delete<CatalogWire>(
      `/v1/skill-catalog/mutes/${encodeURIComponent(slot)}${catalogQuery(folderId)}`,
    )
    .then(toCatalog)
    .then((catalog) => {
      scheduleAccountRulesMemoryRefresh();
      return catalog;
    });
}

export function composeOnDemandSkillContent(
  description: string,
  body: string,
): string {
  const desc = description.replace(/\s+/g, " ").trim();
  const header = desc
    ? `---\napply: on_demand\ndescription: ${desc}\n---\n`
    : "---\napply: on_demand\n---\n";
  return header + body.replace(/^\r?\n/, "");
}

export function skillBodyFromContent(content: string): string {
  if (!content.startsWith("---")) return content;
  const close = content.indexOf("\n---", 3);
  if (close < 0) return content;
  return content.slice(close + 4).replace(/^\r?\n/, "");
}

export function skillFileName(title: string): string {
  const trimmed = title.trim() || "未命名技能";
  return trimmed.toLowerCase().endsWith(".md") ? trimmed : `${trimmed}.md`;
}
