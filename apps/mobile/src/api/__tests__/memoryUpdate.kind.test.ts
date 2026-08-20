import type { components } from "@/types/api.generated";
import { describe, expect, it } from "vitest";
import { toMemoryUpdate } from "../conversations";
import { toMemoryUpdateFeedEntry } from "../memory";

type MemoryUpdateView = components["schemas"]["MemoryUpdateView"];
type MemoryUpdateFeedItem = components["schemas"]["MemoryUpdateFeedItem"];
type MemoryUpdateItemView = components["schemas"]["MemoryUpdateItemView"];

const fingerprint: MemoryUpdateItemView = {
  action: "quota",
  file: "",
  section: "",
  scope: "global",
  content: "fp-hash",
  target: "",
};

describe("memory update kind passthrough", () => {
  it("keeps quota instead of rewriting to semantic", () => {
    const quota: MemoryUpdateView = {
      id: "q1",
      created_at: "2026-07-19T12:00:00Z",
      kind: "quota",
      summary: "常驻条目已满",
      items: [fingerprint],
    };
    expect(toMemoryUpdate(quota).kind).toBe("quota");
  });

  it("keeps quota on the cross-conversation feed entry", () => {
    const quota: MemoryUpdateFeedItem = {
      id: "q1",
      conversation_id: "c1",
      created_at: "2026-07-19T12:00:00Z",
      kind: "quota",
      summary: "常驻条目已满",
      items: [fingerprint],
    };
    expect(toMemoryUpdateFeedEntry(quota).kind).toBe("quota");
  });
});
