import { api } from "@/services/api";
import type { WorkflowDefinition } from "@/services/workflowDefinition";

/** Cross-account workflow shelf. Official playbooks stay on /workflow-playbook-templates. */

export const WORKFLOW_STORE_PAGE_SIZE = 24;

export type WorkflowStoreListingStatus =
  | "published"
  | "unpublished"
  | "taken_down";

export interface WorkflowStoreListing {
  id: string;
  name: string;
  description: string;
  author: string;
  version: string;
  installed: boolean;
  hasUpdate: boolean;
  /** Author's source workflow — match 上架/下架 on「我的」. */
  workflowId: string | null;
  /** Local copy after install. */
  installWorkflowId: string | null;
  status: WorkflowStoreListingStatus;
}

export interface WorkflowStoreListingDetail extends WorkflowStoreListing {
  definition: WorkflowDefinition;
}

export interface WorkflowStorePage {
  items: WorkflowStoreListing[];
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
  source_workflow_id?: string | null;
  workflow_id?: string | null;
  definition?: WorkflowDefinition;
  status?: string;
}

interface PageWire {
  items?: ListingWire[];
  data?: ListingWire[];
  page?: number;
  page_size?: number;
  total?: number;
}

export interface ListWorkflowStoreQuery {
  q?: string;
  page?: number;
  pageSize?: number;
}

function asListingStatus(raw: string | undefined): WorkflowStoreListingStatus {
  if (raw === "unpublished" || raw === "taken_down") return raw;
  return "published";
}

function toListing(w: ListingWire): WorkflowStoreListing {
  return {
    id: w.id,
    name: w.name,
    description: w.description,
    author: w.author ?? "",
    version: w.version_n != null ? String(w.version_n) : (w.version ?? ""),
    installed: Boolean(w.installed),
    hasUpdate: Boolean(w.has_update),
    workflowId: w.source_workflow_id ?? null,
    installWorkflowId: w.workflow_id ?? null,
    status: asListingStatus(w.status),
  };
}

function toDetail(w: ListingWire): WorkflowStoreListingDetail {
  return {
    ...toListing(w),
    definition: w.definition ?? { nodes: [], edges: [] },
  };
}

function asWires(raw: ListingWire[] | PageWire): ListingWire[] {
  if (Array.isArray(raw)) return raw;
  return raw.items ?? raw.data ?? [];
}

function toPage(
  raw: PageWire,
  fallback: ListWorkflowStoreQuery,
): WorkflowStorePage {
  const page = raw.page ?? fallback.page ?? 1;
  const pageSize =
    raw.page_size ?? fallback.pageSize ?? WORKFLOW_STORE_PAGE_SIZE;
  const items = asWires(raw).map(toListing);
  return {
    items,
    page,
    pageSize,
    total: raw.total ?? items.length,
  };
}

export function workflowStoreListQuery(
  opts: ListWorkflowStoreQuery = {},
): string {
  const params = new URLSearchParams();
  const q = opts.q?.trim();
  if (q) params.set("q", q);
  params.set("page", String(opts.page ?? 1));
  params.set("page_size", String(opts.pageSize ?? WORKFLOW_STORE_PAGE_SIZE));
  return `?${params.toString()}`;
}

export function listWorkflowStore(
  opts: ListWorkflowStoreQuery = {},
): Promise<WorkflowStorePage> {
  return api
    .get<PageWire>(`/v1/workflow-store${workflowStoreListQuery(opts)}`)
    .then((raw) => toPage(raw, opts));
}

export function getWorkflowStoreListing(
  id: string,
): Promise<WorkflowStoreListingDetail> {
  return api
    .get<ListingWire>(`/v1/workflow-store/${encodeURIComponent(id)}`)
    .then(toDetail);
}

export function publishWorkflow(
  workflowId: string,
): Promise<WorkflowStoreListing> {
  return api
    .post<ListingWire>("/v1/workflow-store", { workflow_id: workflowId })
    .then(toListing);
}

export function publishWorkflowVersion(
  listingId: string,
): Promise<WorkflowStoreListing> {
  return api
    .post<ListingWire>(
      `/v1/workflow-store/${encodeURIComponent(listingId)}/versions`,
    )
    .then(toListing);
}

export function unpublishWorkflow(listingId: string): Promise<void> {
  return api
    .delete(`/v1/workflow-store/${encodeURIComponent(listingId)}`)
    .then(() => undefined);
}

export function listMyWorkflowListings(): Promise<WorkflowStoreListing[]> {
  return api
    .get<ListingWire[] | PageWire>("/v1/workflow-store/mine")
    .then((raw) => asWires(raw).map(toListing));
}

export function installWorkflow(
  listingId: string,
): Promise<WorkflowStoreListing> {
  return api
    .post<ListingWire>(
      `/v1/workflow-store/${encodeURIComponent(listingId)}/install`,
    )
    .then(toListing);
}

export function listInstalledWorkflows(): Promise<WorkflowStoreListing[]> {
  return api
    .get<ListingWire[] | PageWire>("/v1/workflow-store/installed")
    .then((raw) => asWires(raw).map(toListing));
}

export function reportWorkflow(
  listingId: string,
  reason: string,
): Promise<void> {
  return api
    .post(`/v1/workflow-store/${encodeURIComponent(listingId)}/reports`, {
      reason,
    })
    .then(() => undefined);
}
