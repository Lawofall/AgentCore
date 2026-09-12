import { api } from "@/services/api";
import { scheduleAccountRulesMemoryRefresh } from "@/services/refreshAccountRulesMemory";

/** Cross-account Skill shelf. Orthogonal to overlay replace/mute. */

export const SKILL_STORE_PAGE_SIZE = 24;
export const SKILL_STORE_DISCOVER_PAGE_SIZE = 100;

export type SkillStoreListingStatus =
  | "published"
  | "unpublished"
  | "taken_down";

export type SkillStoreGroup =
  | "legal"
  | "writing"
  | "research"
  | "product"
  | "engineering"
  | "decision";

const SKILL_STORE_GROUP_IDS: readonly SkillStoreGroup[] = [
  "legal",
  "writing",
  "research",
  "product",
  "engineering",
  "decision",
];

export const EMPTY_SKILL_STORE_GROUPS: Record<SkillStoreGroup, number> = {
  legal: 0,
  writing: 0,
  research: 0,
  product: 0,
  engineering: 0,
  decision: 0,
};

export function isSkillStoreGroup(
  value: string | null,
): value is SkillStoreGroup {
  return (
    value != null &&
    (SKILL_STORE_GROUP_IDS as readonly string[]).includes(value)
  );
}

export interface SkillStoreListing {
  id: string;
  name: string;
  description: string;
  author: string;
  version: string;
  group: SkillStoreGroup;
  installed: boolean;
  hasUpdate: boolean;
  /** Author's source document — match 上架/下架 on「我的」. */
  documentId: string | null;
  /** Local copy after install — match 市场徽标 on the prompt tree. */
  installDocumentId: string | null;
  status: SkillStoreListingStatus;
}

export interface SkillStoreListingDetail extends SkillStoreListing {
  content: string;
}

export interface SkillStorePage {
  items: SkillStoreListing[];
  page: number;
  pageSize: number;
  total: number;
  groups: Record<SkillStoreGroup, number>;
}

interface ListingWire {
  id: string;
  name: string;
  description: string;
  author?: string;
  version_n?: number;
  version?: string;
  installed?: boolean;
  has_update?: boolean;
  source_document_id?: string | null;
  document_id?: string | null;
  content?: string;
  status?: string;
  group?: string;
}

interface PageWire {
  items?: ListingWire[];
  data?: ListingWire[];
  page?: number;
  page_size?: number;
  total?: number;
  groups?: Record<string, number>;
}

export interface ListSkillStoreQuery {
  q?: string;
  group?: SkillStoreGroup;
  page?: number;
  pageSize?: number;
}

function asGroup(raw: string | undefined): SkillStoreGroup {
  const value = raw ?? null;
  return isSkillStoreGroup(value) ? value : "writing";
}

function asGroupCounts(
  raw: Record<string, number> | undefined,
): Record<SkillStoreGroup, number> {
  const next = { ...EMPTY_SKILL_STORE_GROUPS };
  if (!raw) return next;
  for (const id of SKILL_STORE_GROUP_IDS) {
    const n = raw[id];
    if (typeof n === "number" && n > 0) next[id] = n;
  }
  return next;
}

function asListingStatus(raw: string | undefined): SkillStoreListingStatus {
  if (raw === "unpublished" || raw === "taken_down") return raw;
  return "published";
}

function toListing(w: ListingWire): SkillStoreListing {
  return {
    id: w.id,
    name: w.name,
    description: w.description,
    author: w.author ?? "",
    version: w.version_n != null ? String(w.version_n) : (w.version ?? ""),
    group: asGroup(w.group),
    installed: Boolean(w.installed),
    hasUpdate: Boolean(w.has_update),
    documentId: w.source_document_id ?? null,
    installDocumentId: w.document_id ?? null,
    status: asListingStatus(w.status),
  };
}

function toDetail(w: ListingWire): SkillStoreListingDetail {
  return { ...toListing(w), content: w.content ?? "" };
}

function asWires(raw: ListingWire[] | PageWire): ListingWire[] {
  if (Array.isArray(raw)) return raw;
  return raw.items ?? raw.data ?? [];
}

function toPage(raw: PageWire, fallback: ListSkillStoreQuery): SkillStorePage {
  const page = raw.page ?? fallback.page ?? 1;
  const pageSize = raw.page_size ?? fallback.pageSize ?? SKILL_STORE_PAGE_SIZE;
  const items = asWires(raw).map(toListing);
  return {
    items,
    page,
    pageSize,
    total: raw.total ?? items.length,
    groups: asGroupCounts(raw.groups),
  };
}

export function skillStoreListQuery(opts: ListSkillStoreQuery = {}): string {
  const params = new URLSearchParams();
  const q = opts.q?.trim();
  if (q) params.set("q", q);
  if (opts.group) params.set("group", opts.group);
  params.set("page", String(opts.page ?? 1));
  params.set("page_size", String(opts.pageSize ?? SKILL_STORE_PAGE_SIZE));
  return `?${params.toString()}`;
}

export function listSkillStore(
  opts: ListSkillStoreQuery = {},
): Promise<SkillStorePage> {
  return api
    .get<PageWire>(`/v1/skill-store${skillStoreListQuery(opts)}`)
    .then((raw) => toPage(raw, opts));
}

export function getSkillStoreListing(
  id: string,
): Promise<SkillStoreListingDetail> {
  return api
    .get<ListingWire>(`/v1/skill-store/${encodeURIComponent(id)}`)
    .then(toDetail);
}

export function publishSkill(
  documentId: string,
  group: SkillStoreGroup,
): Promise<SkillStoreListing> {
  return api
    .post<ListingWire>("/v1/skill-store", {
      document_id: documentId,
      group,
    })
    .then(toListing);
}

export function publishSkillVersion(
  listingId: string,
  documentId: string,
  group?: SkillStoreGroup,
): Promise<SkillStoreListing> {
  return api
    .post<ListingWire>(
      `/v1/skill-store/${encodeURIComponent(listingId)}/versions`,
      { document_id: documentId, group },
    )
    .then(toListing);
}

export function unpublishSkill(listingId: string): Promise<void> {
  return api
    .delete(`/v1/skill-store/${encodeURIComponent(listingId)}`)
    .then(() => undefined);
}

export function listMySkillListings(): Promise<SkillStoreListing[]> {
  return api
    .get<ListingWire[] | PageWire>("/v1/skill-store/mine")
    .then((raw) => asWires(raw).map(toListing));
}

export function installSkill(listingId: string): Promise<SkillStoreListing> {
  return api
    .post<ListingWire>(
      `/v1/skill-store/${encodeURIComponent(listingId)}/install`,
    )
    .then(toListing)
    .then((listing) => {
      scheduleAccountRulesMemoryRefresh();
      return listing;
    });
}

export function listInstalledSkills(): Promise<SkillStoreListing[]> {
  return api
    .get<ListingWire[] | PageWire>("/v1/skill-store/installed")
    .then((raw) => asWires(raw).map(toListing));
}

export function reportSkill(listingId: string, reason: string): Promise<void> {
  return api
    .post(`/v1/skill-store/${encodeURIComponent(listingId)}/reports`, {
      reason,
    })
    .then(() => undefined);
}
