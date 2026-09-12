import { describe, expect, it } from "vitest";
import { elementOutlineLabel, groupOutline } from "../outline";
import type { SceneElement } from "../types";

function sticky(id: string, text: string): SceneElement {
  return {
    id,
    type: "sticky",
    x: 0,
    y: 0,
    width: 140,
    height: 84,
    text,
    schemaVersion: 1,
  };
}

function rect(id: string): SceneElement {
  return {
    id,
    type: "rectangle",
    x: 0,
    y: 0,
    width: 100,
    height: 80,
    schemaVersion: 1,
  };
}

describe("elementOutlineLabel", () => {
  it("uses the first line of text, truncated", () => {
    expect(elementOutlineLabel(sticky("a", "hello"))).toBe("hello");
    expect(elementOutlineLabel(sticky("b", "one\ntwo"))).toBe("one");
    expect(elementOutlineLabel(sticky("c", "abcdefghijklmnopqrstuvwxyz"))).toBe(
      "abcdefghijklmnopqrstuvwx…",
    );
  });

  it("falls back to the type name", () => {
    expect(elementOutlineLabel(rect("r"))).toBe("矩形");
  });
});

describe("groupOutline", () => {
  it("groups by type with front-most first and filters by label", () => {
    const els = [sticky("a", "alpha"), rect("r"), sticky("b", "beta")];
    const groups = groupOutline(els, "");
    expect(groups.map((g) => g.type)).toEqual(["sticky", "rectangle"]);
    expect(groups[0].items.map((e) => e.id)).toEqual(["b", "a"]);

    const filtered = groupOutline(els, "bet");
    expect(filtered).toHaveLength(1);
    expect(filtered[0].items.map((e) => e.id)).toEqual(["b"]);
  });
});
