import {
  SPLIT_DEFAULT_RATIO,
  SPLIT_MIN_PANE_PX,
  besideIdsOnScene,
  canOpenBeside,
  clampSplitRatio,
  closeSplitPane,
  dropMissingPanes,
  openBeside,
  parseConversationSplit,
  promoteDraftPane,
  replaceFocusedPane,
  splitRoomFits,
} from "@/lib/conversationSplit";
import { describe, expect, it } from "vitest";

const split = {
  panes: ["left", "right"] as [string, string],
  focus: 0 as const,
  ratio: 0.5,
};

describe("parseConversationSplit", () => {
  it("rejects junk, two drafts, and the same conversation twice", () => {
    expect(parseConversationSplit(null)).toBeNull();
    expect(
      parseConversationSplit({ panes: [null, null], focus: 0 }),
    ).toBeNull();
    expect(parseConversationSplit({ panes: ["a", "a"], focus: 0 })).toBeNull();
    expect(parseConversationSplit({ panes: ["a"], focus: 0 })).toBeNull();
  });

  it("keeps a draft beside a saved conversation", () => {
    const parsed = parseConversationSplit({
      panes: [null, "a"],
      focus: 1,
      ratio: 0.4,
    });
    expect(parsed?.panes).toEqual([null, "a"]);
    expect(parsed?.focus).toBe(1);
  });
});

describe("openBeside", () => {
  it("puts the current conversation on the left and keeps focus there", () => {
    expect(openBeside(null, "current", "other")).toEqual({
      panes: ["current", "other"],
      focus: 0,
      ratio: SPLIT_DEFAULT_RATIO,
    });
  });

  it("does not open the conversation that is already focused", () => {
    expect(openBeside(null, "current", "current")).toBeNull();
  });

  it("moves focus when the target is already on screen", () => {
    expect(openBeside(split, "left", "right")).toEqual({ ...split, focus: 1 });
  });

  it("replaces the unfocused pane and leaves focus where it is", () => {
    expect(openBeside(split, "left", "third")?.panes).toEqual([
      "left",
      "third",
    ]);
    expect(openBeside(split, "left", "third")?.focus).toBe(0);
  });
});

describe("replaceFocusedPane", () => {
  it("replaces only the focused pane", () => {
    expect(replaceFocusedPane(split, "next")?.panes).toEqual(["next", "right"]);
    expect(replaceFocusedPane(split, "next")?.focus).toBe(0);
  });

  it("moves focus when the route is the other pane", () => {
    expect(replaceFocusedPane(split, "right")?.focus).toBe(1);
    expect(replaceFocusedPane(split, "right")?.panes).toEqual([
      "left",
      "right",
    ]);
  });

  it("focuses the draft that is already beside instead of opening a second one", () => {
    expect(
      replaceFocusedPane({ panes: ["a", null], focus: 0, ratio: 0.5 }, null),
    ).toEqual({ panes: ["a", null], focus: 1, ratio: 0.5 });
  });
});

describe("promoteDraftPane", () => {
  it("fills the draft slot and focuses it", () => {
    expect(
      promoteDraftPane({ panes: ["kept", null], focus: 0, ratio: 0.6 }, "new"),
    ).toEqual({
      panes: ["kept", "new"],
      focus: 1,
      ratio: 0.6,
    });
  });

  it("does nothing when there is no split", () => {
    expect(promoteDraftPane(null, "new")).toBeNull();
  });
});

describe("close and drop", () => {
  it("closing a pane returns the one that stays", () => {
    expect(closeSplitPane(split, 0)).toBe("right");
    expect(closeSplitPane(split, 1)).toBe("left");
  });

  it("navigates when the focused conversation is gone", () => {
    expect(dropMissingPanes(split, (id) => id === "right")).toEqual({
      split: null,
      navigateTo: "right",
    });
  });

  it("stays on the route when only the other pane is gone", () => {
    expect(dropMissingPanes(split, (id) => id === "left")).toEqual({
      split: null,
      navigateTo: "stay",
    });
  });
});

describe("canOpenBeside and scene", () => {
  it("hides the entry when the room is tight or the target is already open", () => {
    expect(canOpenBeside("other", null, "current", false)).toBe(false);
    expect(canOpenBeside("current", null, "current", true)).toBe(false);
    expect(canOpenBeside("left", split, "left", true)).toBe(false);
    expect(canOpenBeside("other", split, "left", true)).toBe(true);
  });

  it("counts the beside pane only while the split is actually drawn", () => {
    expect(besideIdsOnScene("#/conversations/left", split, true)).toEqual([
      "left",
      "right",
    ]);
    expect(besideIdsOnScene("#/conversations/left", split, false)).toEqual([]);
    expect(besideIdsOnScene("#/files", split, true)).toEqual([]);
    expect(
      besideIdsOnScene("#/conversations/left/turn/t1", split, true),
    ).toEqual([]);
  });

  it("needs two readable columns", () => {
    expect(splitRoomFits(SPLIT_MIN_PANE_PX * 2 - 1)).toBe(false);
    expect(splitRoomFits(SPLIT_MIN_PANE_PX * 2)).toBe(true);
    expect(clampSplitRatio(0.1, 1000)).toBeGreaterThanOrEqual(0.4);
    expect(clampSplitRatio(0.9, 1000)).toBeLessThanOrEqual(0.6);
  });
});
