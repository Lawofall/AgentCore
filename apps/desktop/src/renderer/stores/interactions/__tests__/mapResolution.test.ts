import { describe, expect, it } from "vitest";
import { mapEntryResolution } from "../mapResolution";
import type { InteractionEntry } from "../types";

function entry(over: Partial<InteractionEntry> = {}): InteractionEntry {
  return {
    id: "cp-1",
    kind: "ask_user",
    status: "resolved",
    conversationId: "c1",
    messageId: "m1",
    payload: { question: "先做哪条？" },
    ...over,
  };
}

describe("mapEntryResolution", () => {
  it("pending 不读 decision", () => {
    expect(
      mapEntryResolution(
        entry({
          status: "pending",
          resolution: { decision: "continue" },
        }),
      ),
    ).toEqual({ status: "pending", decision: null, note: "" });
  });

  it("resolution.decision 优先", () => {
    expect(
      mapEntryResolution(
        entry({
          resolution: { decision: "continue", note: "就这样" },
          resumeSettled: {
            decision: "stop",
            decidedAt: "",
            turnStatus: "complete",
          },
        }),
      ),
    ).toEqual({ status: "resolved", decision: "continue", note: "就这样" });
  });

  it("resolution 空时读 resumeSettled.decision", () => {
    expect(
      mapEntryResolution(
        entry({
          resumeSettled: {
            decision: "stop",
            decidedAt: "",
            turnStatus: "running",
          },
        }),
      ).decision,
    ).toBe("stop");
  });

  it("空串 / 未识别取值不当成 timeout", () => {
    expect(
      mapEntryResolution(
        entry({
          resumeSettled: {
            decision: "",
            decidedAt: "",
            turnStatus: "unknown",
          },
        }),
      ).decision,
    ).toBeNull();
    expect(
      mapEntryResolution(entry({ resolution: { decision: "maybe" } })).decision,
    ).toBeNull();
  });
});
