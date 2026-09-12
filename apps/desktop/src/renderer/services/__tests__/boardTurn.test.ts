import {
  describeSelection,
  implementSelectionPrompt,
  organizeSelectionPrompt,
  partitionSelection,
  selectionHasImplementBrief,
} from "@/services/boardTurn";
import type { SceneElement } from "@/whiteboard";
import { describe, expect, it } from "vitest";

const el = (p: Partial<SceneElement> & { id: string }): SceneElement => ({
  type: "rectangle",
  x: 0,
  y: 0,
  width: 100,
  height: 60,
  schemaVersion: 1,
  ...p,
});

const scene: SceneElement[] = [
  el({ id: "a", type: "rectangle", x: 10.4, y: 20.6, text: "登录" }),
  el({ id: "b", type: "ellipse", x: 100, y: 200 }),
  el({ id: "c", type: "text", x: 0, y: 0, text: "  说明  " }),
];

describe("describeSelection", () => {
  it("renders one line per selected element with id, shape, rounded pos, and text", () => {
    const out = describeSelection(scene, ["a"]);
    expect(out).toBe("- [a] rectangle @(10,21)：“登录”");
  });

  it("omits text when an element has none", () => {
    expect(describeSelection(scene, ["b"])).toBe("- [b] ellipse @(100,200)");
  });

  it("trims surrounding whitespace from text", () => {
    expect(describeSelection(scene, ["c"])).toBe("- [c] text @(0,0)：“说明”");
  });

  it("skips selection ids not present in the scene (stale selection)", () => {
    const out = describeSelection(scene, ["ghost", "b"]);
    expect(out).toBe("- [b] ellipse @(100,200)");
  });

  it("joins multiple selected elements with newlines", () => {
    const out = describeSelection(scene, ["a", "b"]);
    expect(out.split("\n")).toHaveLength(2);
  });

  it("returns an empty string when nothing resolves", () => {
    expect(describeSelection(scene, ["x", "y"])).toBe("");
  });
});

const mixedScene: SceneElement[] = [
  el({ id: "a", type: "rectangle", x: 10, y: 20, text: "登录" }),
  el({ id: "d", type: "freedraw", x: 5, y: 6 }),
  el({
    id: "img",
    type: "image",
    x: 0,
    y: 0,
    src: "data:image/png;base64,AAA",
  }),
];

describe("partitionSelection", () => {
  it("routes freedraw + image to visual, everything else to structured", () => {
    const { structuredIds, visualIds } = partitionSelection(mixedScene, [
      "a",
      "d",
      "img",
    ]);
    expect(structuredIds).toEqual(["a"]);
    expect(visualIds).toEqual(["d", "img"]);
  });

  it("drops stale ids not present in the scene", () => {
    const { structuredIds, visualIds } = partitionSelection(mixedScene, [
      "a",
      "ghost",
    ]);
    expect(structuredIds).toEqual(["a"]);
    expect(visualIds).toEqual([]);
  });
});

describe("organizeSelectionPrompt", () => {
  it("structured-only: asks for board_ops, embeds the description, no board_read", () => {
    const prompt = organizeSelectionPrompt(scene, ["a"]);
    expect(prompt).toContain("board_ops");
    expect(prompt).toContain("真实 id");
    expect(prompt).toContain("- [a] rectangle @(10,21)：“登录”");
    expect(prompt).not.toContain("board_read");
  });

  it("mixed: tells the CEO to board_read the visual ids (手绘+图片), still describes structured", () => {
    const prompt = organizeSelectionPrompt(mixedScene, ["a", "d", "img"]);
    expect(prompt).toContain("board_read");
    expect(prompt).toContain("d、img"); // the visual ids, listed for the tool call
    expect(prompt).toContain("- [a] rectangle @(10,20)：“登录”"); // structured part kept
  });

  it("pure visual: board_read instruction with no structured section", () => {
    const prompt = organizeSelectionPrompt(mixedScene, ["d", "img"]);
    expect(prompt).toContain("board_read");
    expect(prompt).not.toContain("结构化元素");
  });
});

describe("selectionHasImplementBrief", () => {
  it("blocks structured-only empty shapes (no text, no visual)", () => {
    expect(selectionHasImplementBrief(scene, ["b"])).toBe(false);
    const emptyRect = [el({ id: "r", type: "rectangle", x: 0, y: 0 })];
    expect(selectionHasImplementBrief(emptyRect, ["r"])).toBe(false);
  });

  it("allows sticky/text (or any structured) with non-empty text", () => {
    expect(selectionHasImplementBrief(scene, ["a"])).toBe(true);
    expect(selectionHasImplementBrief(scene, ["c"])).toBe(true);
    const sticky = [el({ id: "s", type: "sticky", text: "做登录页" })];
    expect(selectionHasImplementBrief(sticky, ["s"])).toBe(true);
  });

  it("allows visual path even without text (does not force vision for text cases)", () => {
    expect(selectionHasImplementBrief(mixedScene, ["d"])).toBe(true);
    expect(selectionHasImplementBrief(mixedScene, ["img"])).toBe(true);
    const emptyPlusVisual: SceneElement[] = [
      el({ id: "r", type: "rectangle" }),
      el({ id: "d", type: "freedraw", x: 1, y: 1 }),
    ];
    expect(selectionHasImplementBrief(emptyPlusVisual, ["r", "d"])).toBe(true);
  });

  it("whitespace-only text counts as empty", () => {
    const blank = [el({ id: "t", type: "text", text: "   " })];
    expect(selectionHasImplementBrief(blank, ["t"])).toBe(false);
  });
});

describe("implementSelectionPrompt", () => {
  it("frames the selection as a brief and asks the CEO to lead the team + implement", () => {
    const prompt = implementSelectionPrompt(scene, ["a"]);
    expect(prompt).toContain("brief");
    expect(prompt).toContain("团队");
    expect(prompt).toContain("CEO");
    // structured element is embedded as a real-id requirement line
    expect(prompt).toContain("- [a] rectangle @(10,21)：“登录”");
    expect(prompt).toContain("需求要点");
    // no visual ids → no board_read instruction
    expect(prompt).not.toContain("board_read");
    expect(prompt).toContain("工作区");
  });

  it("mixed: tells the CEO to board_read the visual ids before acting, keeps structured", () => {
    const prompt = implementSelectionPrompt(mixedScene, ["a", "d", "img"]);
    expect(prompt).toContain("board_read");
    expect(prompt).toContain("d、img");
    expect(prompt).toContain("- [a] rectangle @(10,20)：“登录”");
  });

  it("pure visual: board_read instruction with no structured requirement section", () => {
    const prompt = implementSelectionPrompt(mixedScene, ["d", "img"]);
    expect(prompt).toContain("board_read");
    expect(prompt).not.toContain("需求要点");
  });
});
