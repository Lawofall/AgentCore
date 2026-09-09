import { api } from "@/services/api";

export type WorkflowStoreListingStatus =
  | "published"
  | "unpublished"
  | "taken_down";

export type WorkflowStoreListing = {
  id: string;
  name: string;
  description: string;
  author: string;
  author_user_id: string;
  version_n: number;
  status: WorkflowStoreListingStatus;
  updated_at: string;
};

export type WorkflowStoreListingDetail = WorkflowStoreListing & {
  definition: Record<string, unknown>;
};

export type WorkflowStoreListingListResponse = {
  data: WorkflowStoreListing[];
  total: number;
  page: number;
  page_size: number;
};

export type WorkflowStoreReport = {
  id: string;
  listing_id: string;
  listing_name: string;
  listing_status: WorkflowStoreListingStatus;
  user_id: string;
  reporter: string;
  reason: string;
  created_at: string;
};

export type WorkflowStoreReportListResponse = {
  data: WorkflowStoreReport[];
  total: number;
  page: number;
  page_size: number;
};

export type ListWorkflowStoreListingsParams = {
  status?: WorkflowStoreListingStatus;
  page?: number;
  pageSize?: number;
};

export type ListWorkflowStoreReportsParams = {
  page?: number;
  pageSize?: number;
};

function queryString(
  entries: Record<string, string | number | undefined>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(entries)) {
    if (value == null || value === "") continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

/** Admin roster of workflow-store listings (all statuses, including taken_down). */
export async function listWorkflowStoreListings(
  params: ListWorkflowStoreListingsParams = {},
  signal?: AbortSignal,
): Promise<WorkflowStoreListingListResponse> {
  return api.get<WorkflowStoreListingListResponse>(
    `/v1/admin/workflow-store/listings${queryString({
      status: params.status,
      page: params.page ?? 1,
      page_size: params.pageSize,
    })}`,
    signal ? { signal } : undefined,
  );
}

/** User reports against workflow listings — the moderation queue. */
export async function listWorkflowStoreReports(
  params: ListWorkflowStoreReportsParams = {},
  signal?: AbortSignal,
): Promise<WorkflowStoreReportListResponse> {
  return api.get<WorkflowStoreReportListResponse>(
    `/v1/admin/workflow-store/reports${queryString({
      page: params.page ?? 1,
      page_size: params.pageSize,
    })}`,
    signal ? { signal } : undefined,
  );
}

export async function getWorkflowStoreListing(
  listingId: string,
  signal?: AbortSignal,
): Promise<WorkflowStoreListingDetail> {
  return api.get<WorkflowStoreListingDetail>(
    `/v1/admin/workflow-store/listings/${encodeURIComponent(listingId)}`,
    signal ? { signal } : undefined,
  );
}

/**
 * Platform takedown: listing leaves the public shelf. Installed copies stay.
 */
export async function takedownWorkflowStoreListing(
  listingId: string,
): Promise<WorkflowStoreListing> {
  return api.post<WorkflowStoreListing>(
    `/v1/admin/workflow-store/listings/${encodeURIComponent(listingId)}/takedown`,
  );
}
