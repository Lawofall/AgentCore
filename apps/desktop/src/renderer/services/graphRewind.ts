import { ApiError, api } from "@/services/api";

export interface GraphRewindResult {
  scar: string;
  cut_run_ids: string[];
  target_run_id: string;
  files_restored: boolean;
}

/** Record a rewind and wait. Does not send a new user message. */
export async function submitGraphRewind(
  conversationId: string,
  body: {
    messageId: string;
    targetRunId: string;
    restoreFiles: boolean;
  },
): Promise<GraphRewindResult> {
  return api.post<GraphRewindResult>(
    `/v1/conversations/${conversationId}/graph/rewind`,
    {
      message_id: body.messageId,
      target_run_id: body.targetRunId,
      restore_files: body.restoreFiles,
    },
  );
}

export function rewindErrorText(error: unknown): string {
  if (error instanceof ApiError && error.serverMessage) return error.serverMessage;
  if (error instanceof Error && error.message) return error.message;
  return "这次没能从这里继续。";
}
