import { apiFetch } from "@/api/client";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

/** One folder from GET /v1/folders (云端 + 本机). */
export type FolderSummary = Schemas["FolderSummary"];

export async function listFolders(): Promise<FolderSummary[]> {
  const res = await apiFetch("/v1/folders");
  if (!res.ok) throw new Error(`加载文件夹失败 (${res.status})`);
  return (await res.json()) as FolderSummary[];
}

/** Cloud folders only — mobile has no 本机传统 picker / 在此新开. */
export async function listCloudFolders(): Promise<FolderSummary[]> {
  const folders = await listFolders();
  return folders.filter((f) => f.mode === "cloud");
}

export async function getFolder(id: string): Promise<FolderSummary> {
  const res = await apiFetch(`/v1/folders/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`加载文件夹失败 (${res.status})`);
  return (await res.json()) as FolderSummary;
}
