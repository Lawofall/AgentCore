import { describe, expect, it } from "vitest";
import {
  conversationIdFromHash,
  isConversationOnScene,
  isTransientRoute,
  pickAmbientOutlet,
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

  it("covers turn-detail routes", () => {
    expect(conversationIdFromHash("#/conversations/abc/turn/t1")).toBe("abc");
  });

  it("stops the id before a query string", () => {
    expect(conversationIdFromHash("#/conversations/abc?x=1")).toBe("abc");
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
    expect(isTransientRoute("#/preview/onboarding")).toBe(true);
  });

  it("is false for real app routes", () => {
    expect(isTransientRoute("#/conversations/abc")).toBe(false);
    expect(isTransientRoute("#/files")).toBe(false);
  });
});

describe("isConversationOnScene", () => {
  it("is true on the conversation route", () => {
    expect(isConversationOnScene("abc", "#/conversations/abc", [])).toBe(true);
  });

  it("is true when a float follows that conversation", () => {
    expect(isConversationOnScene("abc", "#/files", ["abc"])).toBe(true);
  });

  it("is false on another route with no matching float", () => {
    expect(isConversationOnScene("abc", "#/files", ["other"])).toBe(false);
  });

  it("counts a beside pane that is actually on screen", () => {
    expect(
      isConversationOnScene("beside", "#/conversations/focus", [], ["beside"]),
    ).toBe(true);
    expect(
      isConversationOnScene("beside", "#/conversations/focus/turn/t1", [], []),
    ).toBe(false);
  });
});

describe("pickAmbientOutlet", () => {
  it("silences when the shell is present and the scene is on screen", () => {
    expect(
      pickAmbientOutlet({
        shellPresent: true,
        onScene: true,
        hasOsNotification: true,
        nativeMobile: false,
      }),
    ).toBe("silence");
  });

  it("toasts when present but looking elsewhere", () => {
    expect(
      pickAmbientOutlet({
        shellPresent: true,
        onScene: false,
        hasOsNotification: true,
        nativeMobile: false,
      }),
    ).toBe("toast");
  });

  it("uses OS notification when the shell is away on desktop", () => {
    expect(
      pickAmbientOutlet({
        shellPresent: false,
        onScene: true,
        hasOsNotification: true,
        nativeMobile: false,
      }),
    ).toBe("os");
  });

  it("toasts on Capacitor when away (no Electron OS channel)", () => {
    expect(
      pickAmbientOutlet({
        shellPresent: false,
        onScene: false,
        hasOsNotification: false,
        nativeMobile: true,
      }),
    ).toBe("toast");
  });

  it("stays silent on web when the tab is away", () => {
    expect(
      pickAmbientOutlet({
        shellPresent: false,
        onScene: false,
        hasOsNotification: false,
        nativeMobile: false,
      }),
    ).toBe("silence");
  });
});
