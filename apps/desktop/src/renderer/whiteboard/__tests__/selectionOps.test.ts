import { describe, expect, it } from "vitest";
import {
  align,
  applyStyle,
  clearGroup,
  copyWithOffset,
  distribute,
  nudge,
  patchBox,
  reorder,
  reorderStep,
  setGroup,
  withGroup,
} from "../selectionOps";
import type { SceneElement } from "../types";

/** Build a minimal valid scene element for fixtures. */
const el = (p: Partial<SceneElement> & { id: string }): SceneElement => ({
  type: "rectangle",
  x: 0,
  y: 0,
  width: 100,
  height: 60,
  schemaVersion: 1,
  ...p,
});

const find = (els: readonly SceneElement[], id: string) =>
  els.find((e) => e.id === id);
const ids = (els: readonly SceneElement[]) => els.map((e) => e.id);
const sel = (...members: string[]) => new Set(members);

describe("withGroup", () => {
  it("expands a partial selection to the whole group", () => {
    const els = [
      el({ id: "a", groupIds: ["g1"] }),
      el({ id: "b", groupIds: ["g1"] }),
      el({ id: "c" }),
    ];
    expect(new Set(withGroup(els, ["a"]))).toEqual(new Set(["a", "b"]));
  });

  it("leaves an ungrouped selection unchanged", () => {
    const els = [el({ id: "a" }), el({ id: "b" })];
    expect(withGroup(els, ["a"])).toEqual(["a"]);
  });

  it("dedupes when several members of the same group are passed", () => {
    const els = [
      el({ id: "a", groupIds: ["g"] }),
      el({ id: "b", groupIds: ["g"] }),
    ];
    expect(withGroup(els, ["a", "b"])).toHaveLength(2);
  });
});

describe("reorder", () => {
  const els = () => [el({ id: "a" }), el({ id: "b" }), el({ id: "c" })];

  it("moves the selection to the front (drawn last)", () => {
    expect(ids(reorder(els(), sel("a"), "front"))).toEqual(["b", "c", "a"]);
  });

  it("moves the selection to the back (drawn first)", () => {
    expect(ids(reorder(els(), sel("c"), "back"))).toEqual(["c", "a", "b"]);
  });

  it("preserves relative order within the selected and the rest", () => {
    expect(ids(reorder(els(), sel("a", "c"), "front"))).toEqual([
      "b",
      "a",
      "c",
    ]);
  });
});

describe("align", () => {
  // boxes: a[0,0,100,60] b[50,100,40,20] c[200,30,10,10] → bounds x∈[0,210] y∈[0,120]
  const els = () => [
    el({ id: "a", x: 0, y: 0, width: 100, height: 60 }),
    el({ id: "b", x: 50, y: 100, width: 40, height: 20 }),
    el({ id: "c", x: 200, y: 30, width: 10, height: 10 }),
  ];

  it("aligns left edges to the selection's min x", () => {
    const out = align(els(), sel("a", "b", "c"), "left");
    expect([find(out, "a")?.x, find(out, "b")?.x, find(out, "c")?.x]).toEqual([
      0, 0, 0,
    ]);
  });

  it("aligns horizontal centers to the selection's midline", () => {
    const out = align(els(), sel("a", "b", "c"), "centerX");
    // midX = 105 → x = 105 - width/2
    expect([find(out, "a")?.x, find(out, "b")?.x, find(out, "c")?.x]).toEqual([
      55, 85, 100,
    ]);
  });

  it("aligns bottom edges to the selection's max y", () => {
    const out = align(els(), sel("a", "b", "c"), "bottom");
    // maxY = 120 → y = 120 - height
    expect([find(out, "a")?.y, find(out, "b")?.y, find(out, "c")?.y]).toEqual([
      60, 100, 110,
    ]);
  });

  it("only shifts on the relevant axis (left leaves y untouched)", () => {
    const out = align(els(), sel("a", "b", "c"), "left");
    expect(find(out, "b")?.y).toBe(100);
  });

  it("is a no-op for a single-element selection", () => {
    const input = els();
    expect(align(input, sel("a"), "left")).toEqual(input);
  });

  it("does not mutate the input", () => {
    const a = el({ id: "a", x: 40, y: 0, width: 10, height: 10 });
    align([a, el({ id: "b", x: 0, y: 0 })], sel("a", "b"), "left");
    expect(a.x).toBe(40);
  });
});

describe("distribute", () => {
  it("evens the gaps between ≥3 elements along x (extremes stay put)", () => {
    const els = [
      el({ id: "a", x: 0, y: 0, width: 20, height: 20 }),
      el({ id: "b", x: 30, y: 0, width: 20, height: 20 }),
      el({ id: "c", x: 200, y: 0, width: 20, height: 20 }),
    ];
    // span 0..220, total width 60, gap = (220-60)/2 = 80 → b starts at 0+20+80 = 100
    const out = distribute(els, sel("a", "b", "c"), "x");
    expect(find(out, "a")?.x).toBe(0);
    expect(find(out, "b")?.x).toBe(100);
    expect(find(out, "c")?.x).toBe(200);
  });

  it("distributes along y", () => {
    const els = [
      el({ id: "a", x: 0, y: 0, width: 10, height: 20 }),
      el({ id: "b", x: 0, y: 25, width: 10, height: 20 }),
      el({ id: "c", x: 0, y: 200, width: 10, height: 20 }),
    ];
    const out = distribute(els, sel("a", "b", "c"), "y");
    expect(find(out, "b")?.y).toBe(100);
  });

  it("needs ≥3 — a 2-element selection is unchanged", () => {
    const input = [el({ id: "a", x: 0 }), el({ id: "b", x: 100 })];
    expect(distribute(input, sel("a", "b"), "x")).toEqual(input);
  });
});

describe("reorderStep", () => {
  const els = () => [
    el({ id: "a" }),
    el({ id: "b" }),
    el({ id: "c" }),
    el({ id: "d" }),
  ];

  it("moves the selection forward one step (past one neighbor)", () => {
    expect(ids(reorderStep(els(), sel("a"), "forward"))).toEqual([
      "b",
      "a",
      "c",
      "d",
    ]);
  });

  it("moves the selection backward one step", () => {
    expect(ids(reorderStep(els(), sel("d"), "backward"))).toEqual([
      "a",
      "b",
      "d",
      "c",
    ]);
  });

  it("is a no-op when already at the front", () => {
    expect(ids(reorderStep(els(), sel("d"), "forward"))).toEqual([
      "a",
      "b",
      "c",
      "d",
    ]);
  });

  it("preserves relative order within a multi-element selection", () => {
    expect(ids(reorderStep(els(), sel("a", "b"), "forward"))).toEqual([
      "c",
      "a",
      "b",
      "d",
    ]);
  });
});

describe("applyStyle", () => {
  it("sets fill on the selection only", () => {
    const out = applyStyle([el({ id: "a" }), el({ id: "b" })], sel("a"), {
      fill: "#f00",
    });
    expect(find(out, "a")?.fill).toBe("#f00");
    expect(find(out, "b")?.fill).toBeUndefined();
  });

  it("clears fill when patched with null", () => {
    const out = applyStyle([el({ id: "a", fill: "#f00" })], sel("a"), {
      fill: null,
    });
    expect(find(out, "a")?.fill).toBeUndefined();
  });

  it("leaves stroke untouched when only fill is in the patch", () => {
    const out = applyStyle([el({ id: "a", stroke: "#00f" })], sel("a"), {
      fill: "#f00",
    });
    expect(find(out, "a")?.stroke).toBe("#00f");
  });

  it("does not mutate the input elements", () => {
    const a = el({ id: "a" });
    applyStyle([a], sel("a"), { fill: "#f00" });
    expect(a.fill).toBeUndefined();
  });

  it("sets stroke width and dash style on the selection", () => {
    const out = applyStyle([el({ id: "a" }), el({ id: "b" })], sel("a"), {
      strokeWidth: 7,
      strokeStyle: "dashed",
    });
    expect(find(out, "a")).toMatchObject({
      strokeWidth: 7,
      strokeStyle: "dashed",
    });
    expect(find(out, "b")?.strokeWidth).toBeUndefined();
  });

  it("clears stroke width with null and leaves color untouched", () => {
    const out = applyStyle(
      [el({ id: "a", strokeWidth: 7, stroke: "#f00" })],
      sel("a"),
      {
        strokeWidth: null,
      },
    );
    expect(find(out, "a")?.strokeWidth).toBeUndefined();
    expect(find(out, "a")?.stroke).toBe("#f00");
  });
});

describe("patchBox", () => {
  it("moves and resizes a single selection", () => {
    const out = patchBox(
      [el({ id: "a", x: 0, y: 0, width: 10, height: 10 })],
      sel("a"),
      {
        x: 5,
        width: 40,
      },
    );
    expect(find(out, "a")).toMatchObject({ x: 5, y: 0, width: 40, height: 10 });
  });

  it("does not rotate arrows", () => {
    const arrow: SceneElement = {
      id: "a",
      type: "arrow",
      x: 0,
      y: 0,
      width: 10,
      height: 10,
      schemaVersion: 1,
    };
    const out = patchBox([arrow], sel("a"), { rotation: 1 });
    expect(find(out, "a")?.rotation).toBeUndefined();
  });

  it("ignores multi-selection", () => {
    const els = [el({ id: "a" }), el({ id: "b" })];
    expect(patchBox(els, sel("a", "b"), { x: 9 })).toBe(els);
  });
});

describe("setGroup / clearGroup", () => {
  it("stamps one shared groupId on the selection", () => {
    const out = setGroup(
      [el({ id: "a" }), el({ id: "b" }), el({ id: "c" })],
      sel("a", "b"),
      "g9",
    );
    expect(find(out, "a")?.groupIds).toEqual(["g9"]);
    expect(find(out, "b")?.groupIds).toEqual(["g9"]);
    expect(find(out, "c")?.groupIds).toBeUndefined();
  });

  it("clears group membership from the selection only", () => {
    const out = clearGroup(
      [el({ id: "a", groupIds: ["g"] }), el({ id: "b", groupIds: ["g"] })],
      sel("a"),
    );
    expect(find(out, "a")?.groupIds).toBeUndefined();
    expect(find(out, "b")?.groupIds).toEqual(["g"]);
  });
});

describe("nudge", () => {
  it("translates selected elements and their absolute arrow points", () => {
    const els = [
      el({ id: "a", x: 10, y: 20 }),
      el({
        id: "arr",
        type: "arrow",
        x: 0,
        y: 0,
        width: 0,
        height: 0,
        points: [
          [0, 0],
          [5, 5],
        ],
      }),
      el({ id: "b", x: 0, y: 0 }),
    ];
    const out = nudge(els, sel("a", "arr"), 3, -4);
    expect(find(out, "a")).toMatchObject({ x: 13, y: 16 });
    expect(find(out, "arr")?.points).toEqual([
      [3, -4],
      [8, 1],
    ]);
    expect(find(out, "b")).toMatchObject({ x: 0, y: 0 });
  });

  it("does not mutate the input", () => {
    const a = el({ id: "a", x: 1, y: 2 });
    nudge([a], sel("a"), 5, 5);
    expect(a).toMatchObject({ x: 1, y: 2 });
  });
});

describe("copyWithOffset", () => {
  it("produces a fresh brd- id offset from the source", () => {
    const [copy] = copyWithOffset([el({ id: "a", x: 10, y: 10 })], 16);
    expect(copy.id).toMatch(/^brd-/);
    expect(copy.id).not.toBe("a");
    expect(copy).toMatchObject({ x: 26, y: 26 });
  });

  it("rebinds an arrow endpoint to the copied twin when both are in the set", () => {
    const src = [
      el({ id: "n", x: 0, y: 0 }),
      el({
        id: "arr",
        type: "arrow",
        x: 0,
        y: 0,
        width: 0,
        height: 0,
        points: [
          [0, 0],
          [9, 9],
        ],
        start: { id: "n" },
        end: { id: "n" },
      }),
    ];
    const out = copyWithOffset(src, 16);
    const copyN = out.find((e) => e.type === "rectangle");
    const copyArr = out.find((e) => e.type === "arrow");
    expect(copyArr?.start?.id).toBe(copyN?.id);
    expect(copyArr?.end?.id).toBe(copyN?.id);
  });

  it("keeps an arrow binding to an element outside the set", () => {
    const src = [
      el({
        id: "arr",
        type: "arrow",
        x: 0,
        y: 0,
        width: 0,
        height: 0,
        points: [
          [0, 0],
          [9, 9],
        ],
        start: { id: "outside" },
      }),
    ];
    expect(copyWithOffset(src, 16)[0].start?.id).toBe("outside");
  });

  it("remaps group ids to a fresh shared group", () => {
    const out = copyWithOffset(
      [el({ id: "a", groupIds: ["g"] }), el({ id: "b", groupIds: ["g"] })],
      16,
    );
    const g = out[0].groupIds?.[0];
    expect(g).toBeTruthy();
    expect(g).not.toBe("g");
    expect(out[1].groupIds?.[0]).toBe(g);
  });

  it("does not mutate the source", () => {
    const a = el({ id: "a", x: 10, y: 10, groupIds: ["g"] });
    copyWithOffset([a], 16);
    expect(a).toMatchObject({ id: "a", x: 10, y: 10 });
    expect(a.groupIds).toEqual(["g"]);
  });
});
