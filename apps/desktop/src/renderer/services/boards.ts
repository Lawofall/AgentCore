import { api } from "@/services/api";
import type { components } from "@/types/api.generated";

type Schemas = components["schemas"];

/** Board list/meta row (no scene) — the「白板」list payload. */
export type BoardSummary = Schemas["BoardSummary"];
/** A board plus its full scene (self-built engine format) — the canvas load payload. */
export type BoardDetail = Schemas["BoardDetail"];
/** CAS write outcome; `board` is the live state, populated only on conflict. */
export type BoardWriteResult = Schemas["BoardWriteResult"];
/** The board's dedicated AI conversation id (existing or just-minted). */
export type BoardConversationResponse = Schemas["BoardConversationResponse"];
/** The opaque spatial-JSON scene blob (self-built engine serialized scene). */
export type BoardScene = BoardDetail["scene"];

/** All of the signed-in user's live boards, most-recently-updated first. */
export function listBoards(): Promise<BoardSummary[]> {
  return api.get<BoardSummary[]>("/v1/boards");
}

/** Create a board. Boards are account-scoped — they do not file under a folder. */
export function createBoard(input?: { title?: string }): Promise<BoardSummary> {
  return api.post<BoardSummary>("/v1/boards", {
    title: input?.title ?? null,
  });
}

/** Load a board with its scene (canvas open). */
export function getBoard(id: string): Promise<BoardDetail> {
  return api.get<BoardDetail>(`/v1/boards/${encodeURIComponent(id)}`);
}

/** Rename a board (scene untouched). */
export function renameBoard(id: string, title: string): Promise<BoardSummary> {
  return api.patch<BoardSummary>(`/v1/boards/${encodeURIComponent(id)}`, {
    title,
  });
}

export async function deleteBoard(id: string): Promise<void> {
  await api.delete(`/v1/boards/${encodeURIComponent(id)}`);
}

/** Get (or lazily mint) the board's dedicated AI conversation (AI协作白板 §三 A / M2).
 * Idempotent — call before the board's first AI turn, then run the turn on the
 * returned `conversation_id`. */
export function ensureBoardConversation(
  id: string,
): Promise<BoardConversationResponse> {
  return api.post<BoardConversationResponse>(
    `/v1/boards/${encodeURIComponent(id)}/conversation`,
    {},
  );
}

/** CAS-write the scene (autosave). Pass the `baseline` version the edit was based on;
 * a stale baseline returns `{ok:false, conflict:true, board}` and is NOT applied. */
export function saveBoardScene(
  id: string,
  scene: BoardScene,
  baseline: number | null,
): Promise<BoardWriteResult> {
  return api.put<BoardWriteResult>(
    `/v1/boards/${encodeURIComponent(id)}/scene`,
    {
      scene,
      baseline,
    },
  );
}
