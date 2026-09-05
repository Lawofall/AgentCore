import { api } from "@/services/api";
import { scheduleAccountRulesMemoryRefresh } from "@/services/refreshAccountRulesMemory";

/** Cross-account Skill shelf. Orthogonal to overlay replace/mute. */

export const SKILL_STORE_PAGE_SIZE = 24;

export type SkillStoreListingStatus =
  | "published"
  | "unpublished"
  | "taken_down";

export interface SkillStoreListing {
  id: string;
  name: string;
  description: string;
  author: string;
  version: string;
  installed: boolean;
  hasUpdate: boolean;
  /** Author source document id — used to match「我的技能」上架/下架. */
  documentId: string | null;
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
}

interface PageWire {
  items?: ListingWire[];
  data?: ListingWire[];
  page?: number;
  page_size?: number;
  total?: number;
}

export interface ListSkillStoreQuery {
  q?: string;
  page?: number;
  pageSize?: number;
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
    installed: Boolean(w.installed),
    hasUpdate: Boolean(w.has_update),
    documentId: w.source_document_id ?? null,
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
  };
}

export function skillStoreListQuery(opts: ListSkillStoreQuery = {}): string {
  const params = new URLSearchParams();
  const q = opts.q?.trim();
  if (q) params.set("q", q);
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

export function publishSkill(documentId: string): Promise<SkillStoreListing> {
  return api
    .post<ListingWire>("/v1/skill-store", { document_id: documentId })
    .then(toListing);
}

export function publishSkillVersion(
  listingId: string,
  documentId: string,
): Promise<SkillStoreListing> {
  return api
    .post<ListingWire>(
      `/v1/skill-store/${encodeURIComponent(listingId)}/versions`,
      { document_id: documentId },
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
