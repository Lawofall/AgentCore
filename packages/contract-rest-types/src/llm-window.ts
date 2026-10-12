/**
 * Diagnostic LLM window REST contract.
 *
 * Aligns with `apps/server/agentcore/api/schemas/llm_window.py` and
 * `GET /v1/conversations/{id}/messages/{mid}/runs/{run_id}/llm-window`.
 */

export type LlmWindowRole = "system" | "user" | "assistant" | "tool";

export interface LlmWindowToolCallFunction {
  name: string;
  arguments: string;
}

export interface LlmWindowToolCall {
  id: string;
  type: "function";
  function: LlmWindowToolCallFunction;
}

export interface LlmWindowMessage {
  role: LlmWindowRole;
  content?: string | null;
  tool_calls?: LlmWindowToolCall[] | null;
  tool_call_id?: string | null;
  reasoning_content?: string | null;
  /**
   * Diagnostic merge tag from `run_head.user_origin`.
   * `context_blocks` = opening user was rendered from the structured `run_context`
   * ContextBlock list (UI substitutes those segments; full `content` is the
   * verbatim concat for「查看原始拼接」).
   */
  origin?: string | null;
}

/** `GET .../runs/{run_id}/llm-window` response.

`round_idx` set: the request that call received. Omitted: end-state transcript.
`new_from` / `compacted` / `history_omitted` are set on the per-call read.
*/
export interface RunLlmWindowResponse {
  run_id: string;
  available: boolean;
  messages: LlmWindowMessage[];
  new_from?: number | null;
  compacted?: boolean;
  history_omitted?: boolean;
}
