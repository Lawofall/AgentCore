import { DRAFT_KEY, EMPTY_RUNTIME } from "@/stores/conversation/runtime";
import { pruneConversationSlices } from "@/stores/conversation/sliceLru";
import { describe, expect, it } from "vitest";

describe("pruneConversationSlices", () => {
  it("keeps a visible empty beside slice and a draft column", () => {
    const pruned = pruneConversationSlices(
      {
        [DRAFT_KEY]: { ...EMPTY_RUNTIME },
        beside: { ...EMPTY_RUNTIME },
        stale: { ...EMPTY_RUNTIME },
      },
      [],
      "focus",
      DRAFT_KEY,
      [DRAFT_KEY, "beside"],
    );
    expect(pruned.byId[DRAFT_KEY]).toBeDefined();
    expect(pruned.byId.beside).toBeDefined();
    expect(pruned.byId.stale).toBeUndefined();
  });

  it("drops the draft when leaving it and nothing is showing that column", () => {
    const pruned = pruneConversationSlices(
      { [DRAFT_KEY]: { ...EMPTY_RUNTIME } },
      [],
      "focus",
      DRAFT_KEY,
    );
    expect(pruned.byId[DRAFT_KEY]).toBeUndefined();
  });
});
