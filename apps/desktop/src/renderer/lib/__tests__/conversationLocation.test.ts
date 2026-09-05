import { describe, expect, it } from "vitest";
import {
  conversationLocationId,
  isConversationLocation,
} from "../conversationLocation";

describe("conversationLocationId", () => {
  it("is null off a conversation canvas", () => {
    expect(conversationLocationId("/")).toBeNull();
    expect(conversationLocationId("/conversations")).toBeNull();
    expect(conversationLocationId("/toolbox")).toBeNull();
    expect(conversationLocationId("/toolbox/tools")).toBeNull();
    expect(conversationLocationId("/files")).toBeNull();
    expect(conversationLocationId("/messages")).toBeNull();
    expect(conversationLocationId("/whiteboard")).toBeNull();
    expect(conversationLocationId("/more")).toBeNull();
  });

  it("reads the conversation id from the canvas and turn-detail routes", () => {
    expect(conversationLocationId("/conversations/c1")).toBe("c1");
    expect(conversationLocationId("/conversations/c1/turn/t9")).toBe("c1");
  });
});

describe("isConversationLocation", () => {
  it("matches only the conversation on the canvas", () => {
    expect(isConversationLocation("/conversations/c1", "c1")).toBe(true);
    expect(isConversationLocation("/conversations/c1/turn/t9", "c1")).toBe(
      true,
    );
    expect(isConversationLocation("/conversations/c1", "c2")).toBe(false);
    expect(isConversationLocation("/toolbox", "c1")).toBe(false);
  });
});
