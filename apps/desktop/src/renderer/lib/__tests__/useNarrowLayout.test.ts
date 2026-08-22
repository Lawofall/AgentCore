import {
  isNarrowChatRoute,
  shouldHideNarrowChrome,
} from "@/lib/useNarrowLayout";
import { describe, expect, it } from "vitest";

describe("shouldHideNarrowChrome", () => {
  it("keeps chrome on chat / files / messages list / more index", () => {
    for (const path of [
      "/",
      "/conversations",
      "/conversations/abc",
      "/files",
      "/messages",
      "/more",
    ]) {
      expect(shouldHideNarrowChrome(path)).toBe(false);
    }
  });

  it("hides chrome on detail and preview surfaces", () => {
    for (const path of [
      "/preview",
      "/preview/foo",
      "/simulation/town",
      "/conversations/abc/turn/t1",
      "/messages/chat1",
      "/more/account",
      "/legal/tos",
    ]) {
      expect(shouldHideNarrowChrome(path)).toBe(true);
    }
  });
});

describe("isNarrowChatRoute", () => {
  it("matches draft, list, and a single conversation", () => {
    expect(isNarrowChatRoute("/")).toBe(true);
    expect(isNarrowChatRoute("/conversations")).toBe(true);
    expect(isNarrowChatRoute("/conversations/abc")).toBe(true);
  });

  it("rejects turn canvas and other sections", () => {
    expect(isNarrowChatRoute("/conversations/abc/turn/t1")).toBe(false);
    expect(isNarrowChatRoute("/files")).toBe(false);
    expect(isNarrowChatRoute("/messages")).toBe(false);
  });
});
