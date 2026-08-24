import type { ReplayMessage } from "@/services/adminObservability";

function isBlankAssistant(m: ReplayMessage): boolean {
  if (m.role !== "assistant") return false;
  if ((m.content ?? "").trim()) return false;
  if ((m.reasoning_content ?? "").trim()) return false;
  if ((m.attachments?.length ?? 0) > 0) return false;
  return true;
}

/**
 * Consecutive empty assistant rows after a real assistant are a persist glitch
 * (pause snapshot / duplicate journal), not a second user-visible turn. Fold
 * them into the preceding assistant so process + team tree paint once.
 */
export function foldEmptyAssistantFollowers(messages: ReplayMessage[]): {
  messages: ReplayMessage[];
  shownIdFor: (id: string) => string;
} {
  const out: ReplayMessage[] = [];
  const alias = new Map<string, string>();

  for (const m of messages) {
    const prev = out[out.length - 1];
    if (isBlankAssistant(m) && prev && prev.role === "assistant") {
      alias.set(m.id, prev.id);
      out[out.length - 1] = {
        ...prev,
        runs: prev.runs.length > 0 ? prev.runs : m.runs,
        runs_payload: prev.runs_payload ?? m.runs_payload,
        projected: prev.projected ?? m.projected,
        has_final_state: Boolean(prev.has_final_state || m.has_final_state),
        reasoning_content: prev.reasoning_content ?? m.reasoning_content ?? null,
      };
      continue;
    }
    out.push(m);
  }

  return {
    messages: out,
    shownIdFor: (id) => alias.get(id) ?? id,
  };
}
