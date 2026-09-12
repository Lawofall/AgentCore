// @vitest-environment jsdom
/**
 * Integration tests for {@link WhiteboardEngine} — the stateful canvas controller
 * (AI协作白板.md §六). Its pure sub-modules (ops / scene / clone / layout / snap /
 * selectionOps / transform) are unit-tested separately; this exercises the engine class
 * itself: scene loading, history (undo/redo), selection + reconciliation, the overlay
 * vs. persistent-append distinction, clipboard, and one pointer-drag
 * create flow.
 *
 * `requestAnimationFrame` is neutered so render() (canvas painting, which jsdom has no
 * backend for) never runs — every mutation is synchronous, so we assert scene/history/
 * callbacks, not pixels. getBoundingClientRect is all-zeros in jsdom, so at zoom 1 / pan 0
 * a pointer's clientX/Y equals its world coordinate.
 */

import type { BoardOp } from "@/types/events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WhiteboardEngine } from "../engine";
import type { SceneElement } from "../types";

beforeEach(() => {
  globalThis.requestAnimationFrame = (() => 0) as typeof requestAnimationFrame;
  globalThis.cancelAnimationFrame = (() => {}) as typeof cancelAnimationFrame;
});

afterEach(() => vi.restoreAllMocks());

function makeEngine(opts?: { flushEdit?: () => string | null }) {
  const canvas = document.createElement("canvas");
  // jsdom has no 2D backend; render() is neutered via rAF, so a stub ctx suffices.
  const ctx = {
    measureText: () => ({ width: 0 }),
    save: () => {},
    restore: () => {},
    setTransform: () => {},
    font: "",
  } as unknown as CanvasRenderingContext2D;
  Object.defineProperty(canvas, "getContext", { value: () => ctx });
  const cb = {
    onChange: vi.fn(),
    onSelectionChange: vi.fn(),
    onToolChange: vi.fn(),
    onViewportChange: vi.fn(),
    onContextMenu: vi.fn(),
    onEditingChange: vi.fn(),
    flushEdit: opts?.flushEdit ?? vi.fn(() => null),
  };
  const engine = new WhiteboardEngine(canvas, cb);
  return { engine, cb, canvas };
}

function rect(
  id: string,
  x = 0,
  y = 0,
  width = 100,
  height = 100,
): SceneElement {
  return { id, type: "rectangle", x, y, width, height, schemaVersion: 1 };
}

/** Dispatch a pointer-like event with the fields the engine reads. pointerdown fires on the
 * canvas; move/up are bound on window. */
function pointer(
  target: EventTarget,
  type: string,
  opts: Partial<{
    clientX: number;
    clientY: number;
    button: number;
    shiftKey: boolean;
    altKey: boolean;
  }> = {},
): void {
  const e = new Event(type, { bubbles: true });
  Object.assign(e, {
    clientX: 0,
    clientY: 0,
    button: 0,
    shiftKey: false,
    altKey: false,
    ...opts,
  });
  target.dispatchEvent(e);
}

describe("WhiteboardEngine — tool + viewport", () => {
  it("switches the active tool and notifies the host", () => {
    const { engine, cb } = makeEngine();
    engine.setTool("rectangle");
    expect(engine.getTool()).toBe("rectangle");
    expect(cb.onToolChange).toHaveBeenCalledWith("rectangle");
  });
});

describe("WhiteboardEngine — loadScene", () => {
  it("deep-clones the input and resets selection + viewport", () => {
    const { engine, cb } = makeEngine();
    const input = [rect("a"), rect("b")];
    engine.loadScene(input, { panX: 5, panY: 6, zoom: 2 });

    const scene = engine.getScene();
    expect(scene.map((e) => e.id)).toEqual(["a", "b"]);
    expect(scene[0]).not.toBe(input[0]); // deep-cloned, not aliased
    expect(engine.getViewport()).toEqual({ panX: 5, panY: 6, zoom: 2 });
    expect(engine.getSelectedIds()).toEqual([]);
    expect(cb.onSelectionChange).toHaveBeenCalledWith([]);
    expect(cb.onViewportChange).toHaveBeenCalledWith(2);
  });
});

describe("WhiteboardEngine — applyOps + history", () => {
  it("applies board ops, reports created ids, and is undoable", () => {
    const { engine, cb } = makeEngine();
    const ops: BoardOp[] = [
      { op: "add_node", ref: "a", x: 10, y: 20, text: "hi" },
    ];
    const { created } = engine.applyOps(ops);

    expect(created).toHaveLength(1);
    expect(engine.getScene()).toHaveLength(1);
    expect(engine.getScene()[0]).toMatchObject({ text: "hi" });
    expect(cb.onChange).toHaveBeenCalled();

    engine.undo();
    expect(engine.getScene()).toHaveLength(0);
    engine.redo();
    expect(engine.getScene()).toHaveLength(1);
  });
});

describe("WhiteboardEngine — overlay vs. persistent append", () => {
  it("setOverlay draws transiently: scene + history + onChange untouched", () => {
    const { engine, cb } = makeEngine();
    engine.loadScene([rect("a")]);
    engine.setOverlay([rect("ghost", 500, 500)]);

    expect(engine.getScene().map((e) => e.id)).toEqual(["a"]); // overlay excluded
    expect(cb.onChange).not.toHaveBeenCalled(); // transient — no autosave
  });

  it("addElements appends real, undoable content without stealing selection", () => {
    const { engine, cb } = makeEngine();
    engine.loadScene([rect("a")]);
    const incoming = [rect("b", 200, 0)];
    engine.addElements(incoming);

    const scene = engine.getScene();
    expect(scene.map((e) => e.id)).toEqual(["a", "b"]);
    expect(scene[1]).not.toBe(incoming[0]); // cloned, caller's array not aliased
    expect(engine.getSelectedIds()).toEqual([]); // selection left where it was
    expect(cb.onChange).toHaveBeenCalled();

    engine.undo();
    expect(engine.getScene().map((e) => e.id)).toEqual(["a"]);
  });
});

describe("WhiteboardEngine — selection + delete", () => {
  it("selects all and reports the ids", () => {
    const { engine, cb } = makeEngine();
    engine.loadScene([rect("a"), rect("b")]);
    engine.selectAll();
    expect(engine.getSelectedIds().sort()).toEqual(["a", "b"]);
    expect(cb.onSelectionChange).toHaveBeenLastCalledWith(["a", "b"]);
  });

  it("deletes the selection but spares locked elements", () => {
    const { engine } = makeEngine();
    const locked: SceneElement = { ...rect("b", 200, 0), locked: true };
    engine.loadScene([rect("a"), locked]);
    engine.selectAll();
    engine.deleteSelected();
    expect(engine.getScene().map((e) => e.id)).toEqual(["b"]);
  });

  it("exposes selection bounds and selected type", () => {
    const { engine } = makeEngine();
    engine.loadScene([rect("a", 10, 20, 100, 40)]);
    engine.selectAll();
    expect(engine.hasSelectedType("rectangle")).toBe(true);
    expect(engine.hasSelectedType("image")).toBe(false);
    expect(engine.getSelectionBounds()).toEqual({
      x: 10,
      y: 20,
      width: 100,
      height: 40,
    });
  });
});

describe("WhiteboardEngine — clipboard + grouping", () => {
  it("copy + paste appends an offset duplicate with a fresh id", () => {
    const { engine } = makeEngine();
    engine.loadScene([rect("a")]);
    engine.selectAll();
    engine.copySelection();
    engine.paste();

    const scene = engine.getScene();
    expect(scene).toHaveLength(2);
    expect(scene[1].id).not.toBe("a");
    expect(engine.getSelectedIds()).toEqual([scene[1].id]); // paste selects the copy
  });

  it("groups the selection under a shared id and ungroups it back", () => {
    const { engine } = makeEngine();
    engine.loadScene([rect("a"), rect("b", 200, 0)]);
    engine.selectAll();
    engine.groupSelected();

    const groups = engine.getScene().map((e) => e.groupIds?.[0]);
    expect(groups[0]).toBeTruthy();
    expect(groups[1]).toBe(groups[0]);

    engine.ungroupSelected();
    expect(
      engine.getScene().every((e) => (e.groupIds ?? []).length === 0),
    ).toBe(true);
  });

  it("nudges the selection and records one history step", () => {
    const { engine } = makeEngine();
    engine.loadScene([rect("a", 0, 0)]);
    engine.selectAll();
    engine.nudgeSelected(5, -3);
    expect(engine.getScene()[0]).toMatchObject({ x: 5, y: -3 });
    engine.undo();
    expect(engine.getScene()[0]).toMatchObject({ x: 0, y: 0 });
  });
});

describe("WhiteboardEngine — pointer create flow", () => {
  it("draws a rectangle from pointer down → move → up, then returns to select", () => {
    const { engine, cb, canvas } = makeEngine();
    engine.setTool("rectangle");

    pointer(canvas, "pointerdown", { clientX: 0, clientY: 0, button: 0 });
    pointer(window, "pointermove", { clientX: 50, clientY: 40 });
    pointer(window, "pointerup");

    const scene = engine.getScene();
    expect(scene).toHaveLength(1);
    expect(scene[0]).toMatchObject({ width: 50, height: 40 });
    expect(engine.getSelectedIds()).toEqual([scene[0].id]);
    expect(engine.getTool()).toBe("select"); // create snaps back to select
    expect(cb.onChange).toHaveBeenCalled();

    engine.undo();
    expect(engine.getScene()).toHaveLength(0);
  });
});

describe("WhiteboardEngine — pointer resize", () => {
  it("drags the SE handle of a single selection to resize it (undoable)", () => {
    const { engine, cb, canvas } = makeEngine();
    engine.loadScene([rect("a", 0, 0, 100, 100)]);
    engine.selectAll(); // size 1 → resize handles are live

    // At zoom 1 / pan 0 the SE handle sits at screen (100,100) = the box's bottom-right.
    pointer(canvas, "pointerdown", { clientX: 100, clientY: 100 });
    pointer(window, "pointermove", { clientX: 150, clientY: 150 });
    pointer(window, "pointerup");

    expect(engine.getScene()[0]).toMatchObject({
      x: 0,
      y: 0,
      width: 150,
      height: 150,
    });
    expect(cb.onChange).toHaveBeenCalled();
    engine.undo();
    expect(engine.getScene()[0]).toMatchObject({ width: 100, height: 100 });
  });
});

describe("WhiteboardEngine — marquee selection", () => {
  it("rubber-bands over empty space and selects the intersected elements", () => {
    const { engine, canvas } = makeEngine();
    engine.loadScene([rect("a", 0, 0, 100, 100), rect("b", 200, 0, 100, 100)]);

    pointer(canvas, "pointerdown", { clientX: 400, clientY: 400 }); // empty start
    pointer(window, "pointermove", { clientX: -10, clientY: -10 }); // box covers a + b
    pointer(window, "pointerup");

    expect(engine.getSelectedIds().sort()).toEqual(["a", "b"]);
  });
});

describe("WhiteboardEngine — move drag", () => {
  it("drags the hit element and commits one undoable translate", () => {
    const { engine, cb, canvas } = makeEngine();
    engine.loadScene([rect("a", 0, 0, 100, 100)]);

    pointer(canvas, "pointerdown", { clientX: 50, clientY: 50 }); // hits + selects a
    pointer(window, "pointermove", { clientX: 70, clientY: 60 }); // +20, +10
    pointer(window, "pointerup");

    expect(engine.getSelectedIds()).toEqual(["a"]);
    expect(engine.getScene()[0]).toMatchObject({ x: 20, y: 10 });
    expect(cb.onChange).toHaveBeenCalled();
    engine.undo();
    expect(engine.getScene()[0]).toMatchObject({ x: 0, y: 0 });
  });
});

describe("WhiteboardEngine — arrow draw + binding", () => {
  it("draws an arrow between two shapes and binds both endpoints", () => {
    const { engine, canvas } = makeEngine();
    engine.loadScene([rect("a", 0, 0, 100, 100), rect("b", 300, 0, 100, 100)]);
    engine.setTool("arrow");

    pointer(canvas, "pointerdown", { clientX: 50, clientY: 50 }); // inside a
    pointer(window, "pointermove", { clientX: 350, clientY: 50 }); // inside b
    pointer(window, "pointerup");

    const arrow = engine.getScene().find((e) => e.type === "arrow");
    expect(arrow?.start?.id).toBe("a");
    expect(arrow?.end?.id).toBe("b");
    expect(engine.getTool()).toBe("select"); // drops back after drawing
  });
});

describe("WhiteboardEngine — freedraw", () => {
  it("captures a pen stroke as one undoable freedraw element", () => {
    const { engine, cb, canvas } = makeEngine();
    engine.setTool("freedraw");

    pointer(canvas, "pointerdown", { clientX: 10, clientY: 10 });
    pointer(window, "pointermove", { clientX: 20, clientY: 15 });
    pointer(window, "pointermove", { clientX: 30, clientY: 30 });
    pointer(window, "pointerup");

    const scene = engine.getScene();
    expect(scene).toHaveLength(1);
    expect(scene[0].type).toBe("freedraw");
    expect(cb.onChange).toHaveBeenCalled();
  });

  it("drops a zero-length stroke from a bare pen click", () => {
    const { engine, canvas } = makeEngine();
    engine.setTool("freedraw");

    pointer(canvas, "pointerdown", { clientX: 5, clientY: 5 });
    pointer(window, "pointerup");

    expect(engine.getScene()).toHaveLength(0);
  });
});

describe("WhiteboardEngine — eraser", () => {
  it("removes the element under the pointer (undoable)", () => {
    const { engine, cb, canvas } = makeEngine();
    engine.loadScene([rect("a", 0, 0, 100, 100)]);
    engine.setTool("eraser");

    pointer(canvas, "pointerdown", { clientX: 50, clientY: 50 });
    pointer(window, "pointerup");

    expect(engine.getScene()).toHaveLength(0);
    expect(cb.onChange).toHaveBeenCalled();
    engine.undo();
    expect(engine.getScene().map((e) => e.id)).toEqual(["a"]);
  });
});

describe("WhiteboardEngine — pan", () => {
  it("pans the viewport with the hand tool WITHOUT firing onChange", () => {
    const { engine, cb, canvas } = makeEngine();
    engine.setTool("hand");

    pointer(canvas, "pointerdown", { clientX: 0, clientY: 0 });
    pointer(window, "pointermove", { clientX: 30, clientY: 20 });
    pointer(window, "pointerup");

    expect(engine.getViewport()).toMatchObject({ panX: 30, panY: 20 });
    expect(cb.onChange).not.toHaveBeenCalled(); // navigation never autosaves
  });
});

describe("WhiteboardEngine — alt-drag duplicate", () => {
  it("Alt-dragging an element drops an in-place copy and drags the copy", () => {
    const { engine, cb, canvas } = makeEngine();
    engine.loadScene([rect("a", 0, 0, 100, 100)]);

    pointer(canvas, "pointerdown", { clientX: 50, clientY: 50, altKey: true });
    pointer(window, "pointermove", { clientX: 60, clientY: 50 });
    pointer(window, "pointerup");

    const scene = engine.getScene();
    expect(scene).toHaveLength(2);
    const selected = engine.getSelectedIds();
    expect(selected).toHaveLength(1);
    expect(selected[0]).not.toBe("a"); // the copy is selected, not the original
    expect(cb.onChange).toHaveBeenCalled();

    engine.undo(); // one history step covers the copy + the move
    expect(engine.getScene().map((e) => e.id)).toEqual(["a"]);
  });
});

describe("WhiteboardEngine — multi-select scale", () => {
  it("drags the union-frame handle to scale every selected element from the anchor", () => {
    const { engine, cb, canvas } = makeEngine();
    engine.loadScene([rect("a", 0, 0, 100, 100), rect("b", 200, 0, 100, 100)]);
    engine.selectAll(); // union {0,0,300,100}; SE handle sits at screen (300,100)

    pointer(canvas, "pointerdown", { clientX: 300, clientY: 100 });
    pointer(window, "pointermove", { clientX: 600, clientY: 100 }); // scaleX = 2
    pointer(window, "pointerup");

    const scene = Object.fromEntries(engine.getScene().map((e) => [e.id, e]));
    expect(scene.a).toMatchObject({ x: 0, width: 200 });
    expect(scene.b).toMatchObject({ x: 400, width: 200 });
    expect(cb.onChange).toHaveBeenCalled();

    engine.undo();
    const after = Object.fromEntries(engine.getScene().map((e) => [e.id, e]));
    expect(after.a).toMatchObject({ x: 0, width: 100 });
    expect(after.b).toMatchObject({ x: 200, width: 100 });
  });
});

describe("WhiteboardEngine — text editing", () => {
  it("creates a text element from flushEdit", () => {
    const { engine, cb } = makeEngine({ flushEdit: () => "  hi  " });
    engine.startEdit(null, [5, 6]);
    expect(cb.onEditingChange).toHaveBeenCalledWith({
      id: null,
      world: [5, 6],
    });
    engine.commitEditing();
    expect(engine.getScene()[0]).toMatchObject({
      type: "text",
      text: "hi",
      x: 5,
      y: 6,
    });
    expect(engine.getEditing()).toBeNull();
    expect(cb.onEditingChange).toHaveBeenLastCalledWith(null);
  });

  it("does not wipe existing text when the overlay never attached", () => {
    const { engine } = makeEngine();
    engine.loadScene([
      {
        id: "t",
        type: "text",
        x: 0,
        y: 0,
        width: 40,
        height: 24,
        text: "keep",
        fontSize: 18,
        schemaVersion: 1,
      },
    ]);
    engine.startEdit(engine.getScene()[0]);
    engine.commitEditing();
    expect(engine.getScene()[0].text).toBe("keep");
  });

  it("updates a sticky label", () => {
    const { engine } = makeEngine({ flushEdit: () => "note" });
    engine.loadScene([
      {
        id: "s",
        type: "sticky",
        x: 0,
        y: 0,
        width: 140,
        height: 84,
        text: "old",
        schemaVersion: 1,
      },
    ]);
    engine.startEdit(engine.getScene()[0]);
    engine.commitEditing();
    expect(engine.getScene()[0].text).toBe("note");
  });

  it("deletes an emptied text element", () => {
    const { engine } = makeEngine({ flushEdit: () => "  " });
    engine.loadScene([
      {
        id: "t",
        type: "text",
        x: 0,
        y: 0,
        width: 40,
        height: 24,
        text: "x",
        fontSize: 18,
        schemaVersion: 1,
      },
    ]);
    engine.startEdit(engine.getScene()[0]);
    engine.commitEditing();
    expect(engine.getScene()).toHaveLength(0);
  });

  it("text tool on a sticky edits it instead of creating a new text", () => {
    const { engine, canvas } = makeEngine();
    engine.loadScene([
      {
        id: "s",
        type: "sticky",
        x: 0,
        y: 0,
        width: 140,
        height: 84,
        text: "old",
        schemaVersion: 1,
      },
    ]);
    engine.setTool("text");
    pointer(canvas, "pointerdown", { clientX: 10, clientY: 10 });
    expect(engine.getEditing()?.id).toBe("s");
  });

  it("double-click empty canvas starts a new text", () => {
    const { engine, canvas } = makeEngine();
    canvas.dispatchEvent(
      new MouseEvent("dblclick", { clientX: 12, clientY: 34, bubbles: true }),
    );
    expect(engine.getEditing()).toEqual({ id: null, world: [12, 34] });
  });

  it("empty new text is a no-op", () => {
    const { engine } = makeEngine({ flushEdit: () => "  " });
    engine.startEdit(null, [0, 0]);
    engine.commitEditing();
    expect(engine.getScene()).toEqual([]);
  });
});
