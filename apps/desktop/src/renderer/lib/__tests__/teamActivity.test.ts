import { describe, expect, it } from "vitest";
import {
  conversationIdFromHash,
  isTransientRoute,
  runtimeHasError,
} from "../teamActivity";

describe("runtimeHasError", () => {
  it("is false for a clean completed turn", () => {
    expect(
      runtimeHasError({
        error: null,
        messages: [{ role: "user" }, { role: "assistant", error: undefined }],
      }),
    ).toBe(false);
  });

  it("detects the SSE error path (last assistant message stamped)", () => {
    expect(
      runtimeHasError({
        error: null,
        messages: [
          { role: "user" },
          { role: "assistant", error: { code: "x", message: "boom" } },
        ],
      }),
    ).toBe(true);
  });

  it("detects the transport-drop path (runtime-level error string)", () => {
    expect(
      runtimeHasError({ error: "网络中断", messages: [{ role: "user" }] }),
    ).toBe(true);
  });

  it("reads only the LAST assistant message", () => {
    expect(
      runtimeHasError({
        error: null,
        messages: [
          { role: "assistant", error: { code: "old", message: "prev" } },
          { role: "assistant", error: undefined },
        ],
      }),
    ).toBe(false);
  });
});

describe("conversationIdFromHash", () => {
  it("extracts the id from a conversation route", () => {
    expect(conversationIdFromHash("#/conversations/abc123")).toBe("abc123");
  });

  it("ignores the msg query anchor", () => {
    expect(conversationIdFromHash("#/conversations/abc?msg=m1")).toBe("abc");
  });

  it("returns null off the conversation route", () => {
    expect(conversationIdFromHash("#/files")).toBeNull();
    expect(conversationIdFromHash("#/")).toBeNull();
    expect(conversationIdFromHash("#/conversations")).toBeNull();
  });
});

describe("isTransientRoute", () => {
  it("flags preview surfaces", () => {
    expect(isTransientRoute("#/preview")).toBe(true);
    expect(isTransientRoute("#/preview/whiteboard")).toBe(true);
  });

  it("is false for real app routes", () => {
    expect(isTransientRoute("#/conversations/abc")).toBe(false);
    expect(isTransientRoute("#/files")).toBe(false);
  });
});
