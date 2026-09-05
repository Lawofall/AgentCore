import { api } from "@/services/api";

export type SkillStoreListingStatus =
  | "published"
  | "unpublished"
  | "taken_down";

export type SkillStoreListing = {
  id: string;
  name: string;
  description: string;
  author: string;
  author_user_id: string;
  version_n: number;
  status: SkillStoreListingStatus;
  updated_at: string;
};

export type SkillStoreListingDetail = SkillStoreListing & {
  content: string;
};

export type SkillStoreListingListResponse = {
  data: SkillStoreListing[];
  total: number;
  page: number;
  page_size: number;
};

export type SkillStoreReport = {
  id: string;
  listing_id: string;
  listing_name: string;
  listing_status: SkillStoreListingStatus;
  user_id: string;
  reporter: string;
  reason: string;
  created_at: string;
};

export type SkillStoreReportListResponse = {
  data: SkillStoreReport[];
  total: number;
  page: number;
  page_size: number;
};

export type ListSkillStoreListingsParams = {
  status?: SkillStoreListingStatus;
  page?: number;
  pageSize?: number;
};

export type ListSkillStoreReportsParams = {
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

/** Admin roster of skill-store listings (all statuses, including taken_down). */
export async function listSkillStoreListings(
  params: ListSkillStoreListingsParams = {},
  signal?: AbortSignal,
): Promise<SkillStoreListingListResponse> {
  return api.get<SkillStoreListingListResponse>(
    `/v1/admin/skill-store/listings${queryString({
      status: params.status,
      page: params.page ?? 1,
      page_size: params.pageSize,
    })}`,
    signal ? { signal } : undefined,
  );
}

/** User reports against listings — the moderation queue. */
export async function listSkillStoreReports(
  params: ListSkillStoreReportsParams = {},
  signal?: AbortSignal,
): Promise<SkillStoreReportListResponse> {
  return api.get<SkillStoreReportListResponse>(
    `/v1/admin/skill-store/reports${queryString({
      page: params.page ?? 1,
      page_size: params.pageSize,
    })}`,
    signal ? { signal } : undefined,
  );
}

export async function getSkillStoreListing(
  listingId: string,
  signal?: AbortSignal,
): Promise<SkillStoreListingDetail> {
  return api.get<SkillStoreListingDetail>(
    `/v1/admin/skill-store/listings/${encodeURIComponent(listingId)}`,
    signal ? { signal } : undefined,
  );
}

/**
 * Platform takedown: listing leaves the public shelf. Installed copies stay.
 */
export async function takedownSkillStoreListing(
  listingId: string,
): Promise<SkillStoreListing> {
  return api.post<SkillStoreListing>(
    `/v1/admin/skill-store/listings/${encodeURIComponent(listingId)}/takedown`,
  );
}
