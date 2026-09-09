/** Internal payload for 提示词目录 reparent (not sibling reorder). */
export const PROMPT_DRAG_MIME = "application/x-agentcore-prompt";
/** Official HOW — separate MIME so dragover can reject the root without reading data. */
export const PROMPT_SKILL_DRAG_MIME = "application/x-agentcore-prompt-skill";

export type PromptDragPayload =
  | { kind: "mine"; mineId: string }
  | { kind: "skill"; slot: string };

export function promptDragPayload(payload: PromptDragPayload): string {
  return JSON.stringify(payload);
}

export function parsePromptDragPayload(raw: string): PromptDragPayload | null {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const record = parsed as {
      kind?: unknown;
      mineId?: unknown;
      slot?: unknown;
    };
    if (record.kind === "skill") {
      return typeof record.slot === "string" && record.slot !== ""
        ? { kind: "skill", slot: record.slot }
        : null;
    }
    if (record.kind === "mine" || record.kind == null) {
      return typeof record.mineId === "string" && record.mineId !== ""
        ? { kind: "mine", mineId: record.mineId }
        : null;
    }
    return null;
  } catch {
    return null;
  }
}

export function isPromptDrag(types: readonly string[]): boolean {
  return (
    types.includes(PROMPT_DRAG_MIME) || types.includes(PROMPT_SKILL_DRAG_MIME)
  );
}

export function isPromptSkillDrag(types: readonly string[]): boolean {
  return types.includes(PROMPT_SKILL_DRAG_MIME);
}
