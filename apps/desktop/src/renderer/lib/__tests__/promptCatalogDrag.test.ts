import {
  PROMPT_DRAG_MIME,
  PROMPT_SKILL_DRAG_MIME,
  isPromptDrag,
  isPromptSkillDrag,
  parsePromptDragPayload,
  promptDragPayload,
} from "@/lib/promptCatalogDrag";
import { describe, expect, it } from "vitest";

describe("promptCatalogDrag", () => {
  it("round-trips mine kind", () => {
    expect(
      parsePromptDragPayload(promptDragPayload({ kind: "mine", mineId: "d1" })),
    ).toEqual({ kind: "mine", mineId: "d1" });
  });

  it("accepts legacy { mineId }", () => {
    expect(parsePromptDragPayload(JSON.stringify({ mineId: "d1" }))).toEqual({
      kind: "mine",
      mineId: "d1",
    });
  });

  it("round-trips skill slot", () => {
    expect(
      parsePromptDragPayload(
        promptDragPayload({ kind: "skill", slot: "staffing" }),
      ),
    ).toEqual({ kind: "skill", slot: "staffing" });
  });

  it("rejects junk", () => {
    expect(parsePromptDragPayload("")).toBeNull();
    expect(parsePromptDragPayload("{}")).toBeNull();
    expect(parsePromptDragPayload("{")).toBeNull();
    expect(
      parsePromptDragPayload(JSON.stringify({ kind: "skill" })),
    ).toBeNull();
  });

  it("skill MIME is recognized for dragover", () => {
    expect(isPromptDrag([PROMPT_DRAG_MIME])).toBe(true);
    expect(isPromptDrag([PROMPT_SKILL_DRAG_MIME])).toBe(true);
    expect(isPromptSkillDrag([PROMPT_SKILL_DRAG_MIME])).toBe(true);
    expect(isPromptSkillDrag([PROMPT_DRAG_MIME])).toBe(false);
  });
});
