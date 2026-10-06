import { describe, expect, it } from "vitest";
import { type CutMessageRef, contextCutAvailable } from "../contextCut";

function row(
  id: string,
  role: "user" | "assistant",
  minute: number,
): CutMessageRef {
  return {
    id,
    role,
    createdAt: `2026-10-04T00:0${minute}:00Z`,
  };
}

const u1 = row("u1", "user", 1);
const a1 = row("a1", "assistant", 2);
const u2 = row("u2", "user", 3);
const a2 = row("a2", "assistant", 4);

describe("contextCutAvailable", () => {
  it("shows on the message that restates the plan", () => {
    expect(
      contextCutAvailable({
        message: a2,
        messages: [u1, a1, u2, a2],
        hasMoreBefore: false,
        blocked: false,
      }),
    ).toBe(true);
  });

  it("hides when the only live line before an assistant reply is its question", () => {
    expect(
      contextCutAvailable({
        message: a1,
        messages: [u1, a1],
        hasMoreBefore: false,
        blocked: false,
      }),
    ).toBe(false);
  });

  it("hides lines already behind the watermark", () => {
    expect(
      contextCutAvailable({
        message: a2,
        messages: [u1, a1, u2, a2],
        compactedThrough: "2026-10-04T00:02:30Z",
        hasMoreBefore: false,
        blocked: false,
      }),
    ).toBe(false);
  });

  it("hides while the desk is busy or the reply is still running", () => {
    expect(
      contextCutAvailable({
        message: a2,
        messages: [u1, a1, u2, a2],
        hasMoreBefore: false,
        blocked: true,
      }),
    ).toBe(false);
    expect(
      contextCutAvailable({
        message: { ...a2, status: "running" },
        messages: [u1, a1, u2, a2],
        hasMoreBefore: false,
        blocked: false,
      }),
    ).toBe(false);
  });
});
