import { api } from "@/services/api";
import type { RunLlmWindowResponse } from "@agentcore/contract-rest-types/llm-window";

export type { RunLlmWindowResponse };

export interface JournalLine {
  id: string;
  kind: string;
  ts?: string | null;
  /** One identifying field: a round, a tool name, or a run. */
  mark?: string | null;
  payload: Record<string, unknown>;
  /** Set only on llm_call, for the folded-window request. */
  run_id?: string | null;
  round_idx?: number | null;
}

export interface DeveloperTrace {
  trace_id: string | null;
  where?: string | null;
  model?: string | null;
  duration_ms?: number | null;
  in_progress: boolean;
  gap?: string | null;
  lines: JournalLine[];
}

export function fetchDeveloperTrace(
  conversationId: string,
  messageId: string,
): Promise<DeveloperTrace> {
  return api.get<DeveloperTrace>(
    `/v1/conversations/${conversationId}/messages/${messageId}/trace`,
  );
}

export function fetchCallWindow(
  conversationId: string,
  messageId: string,
  runId: string,
  roundIdx: number,
): Promise<RunLlmWindowResponse> {
  const query = new URLSearchParams({ round_idx: String(roundIdx) });
  return api.get<RunLlmWindowResponse>(
    `/v1/conversations/${conversationId}/messages/${messageId}/runs/${encodeURIComponent(runId)}/llm-window?${query}`,
  );
}
