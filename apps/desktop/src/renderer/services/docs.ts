import type { DocBody } from "@/lib/docBody";
import { api } from "@/services/api";
import type { CreateShareOptions, Share } from "@/services/sharing";

/** List/meta row (no body) — the「文档」list payload. */
export type DocSummary = {
  id: string;
  title: string;
  folder_id: string;
  folder_name: string;
  version: number;
  can_write: boolean;
  created_at: string;
  updated_at: string;
};

/** A doc plus its full block body (editor load payload). */
export type DocDetail = DocSummary & { body: DocBody };

export type DocWriteResult = {
  ok: boolean;
  version: number;
  conflict: boolean;
  doc?: DocDetail | null;
};

export function listDocs(folderId?: string): Promise<DocSummary[]> {
  const q = folderId ? `?folder_id=${encodeURIComponent(folderId)}` : "";
  return api.get<DocSummary[]>(`/v1/docs${q}`);
}

export function createDoc(input: {
  folder_id: string;
  title?: string;
}): Promise<DocSummary> {
  return api.post<DocSummary>("/v1/docs", {
    folder_id: input.folder_id,
    title: input.title ?? null,
  });
}

export function getDoc(id: string): Promise<DocDetail> {
  return api.get<DocDetail>(`/v1/docs/${encodeURIComponent(id)}`);
}

export function renameDoc(id: string, title: string): Promise<DocSummary> {
  return api.patch<DocSummary>(`/v1/docs/${encodeURIComponent(id)}`, { title });
}

export async function deleteDoc(id: string): Promise<void> {
  await api.delete(`/v1/docs/${encodeURIComponent(id)}`);
}

export function saveDocBody(
  id: string,
  body: DocBody,
  baseline: number | null,
): Promise<DocWriteResult> {
  return api.put<DocWriteResult>(`/v1/docs/${encodeURIComponent(id)}/body`, {
    body,
    baseline,
  });
}

export async function createDocShare(
  docId: string,
  options?: CreateShareOptions,
): Promise<Share> {
  return api.post<Share>(
    `/v1/docs/${encodeURIComponent(docId)}/shares`,
    options,
  );
}

export async function listDocShares(docId: string): Promise<Share[]> {
  const res = await api.get<{ data: Share[]; total: number }>(
    `/v1/docs/${encodeURIComponent(docId)}/shares`,
  );
  return res.data;
}

export async function revokeDocShare(
  docId: string,
  shareId: string,
): Promise<void> {
  await api.delete(
    `/v1/docs/${encodeURIComponent(docId)}/shares/${encodeURIComponent(shareId)}`,
  );
}
