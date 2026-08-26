// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import {
  HorizontalTabStrip,
  SortableTab,
  TAB_DRAG_THRESHOLD_PX,
  moveItem,
  placeAlongAxis,
  useSortableTabIds,
} from "../horizontal-tab-strip";

beforeAll(() => {
  // jsdom 无 ResizeObserver；hook 会挂观察器。
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;

  // jsdom 对 Pointer Capture / elementsFromPoint 支持不完整。
  Element.prototype.setPointerCapture ??= function setPointerCapture() {};
  Element.prototype.releasePointerCapture ??=
    function releasePointerCapture() {};
  Element.prototype.hasPointerCapture ??= function hasPointerCapture() {
    return false;
  };
  document.elementsFromPoint ??= () => [];
});

describe("moveItem", () => {
  const ids = ["a", "b", "c", "d"];

  it("moves before a later target", () => {
    expect(moveItem(ids, "a", "c", "before")).toEqual(["b", "a", "c", "d"]);
  });

  it("moves after a later target", () => {
    expect(moveItem(ids, "a", "c", "after")).toEqual(["b", "c", "a", "d"]);
  });

  it("moves before an earlier target", () => {
    expect(moveItem(ids, "d", "b", "before")).toEqual(["a", "d", "b", "c"]);
  });

  it("moves after an earlier target", () => {
    expect(moveItem(ids, "d", "b", "after")).toEqual(["a", "b", "d", "c"]);
  });

  it("returns a copy when from === over", () => {
    const next = moveItem(ids, "b", "b", "after");
    expect(next).toEqual(ids);
    expect(next).not.toBe(ids);
  });

  it("returns a copy when an id is unknown", () => {
    expect(moveItem(ids, "x", "a", "before")).toEqual(ids);
    expect(moveItem(ids, "a", "x", "after")).toEqual(ids);
  });

  it("is a stable no-op when already adjacent before", () => {
    expect(moveItem(ids, "b", "c", "before")).toEqual(["a", "b", "c", "d"]);
  });

  it("is a stable no-op when already adjacent after", () => {
    expect(moveItem(ids, "c", "b", "after")).toEqual(["a", "b", "c", "d"]);
  });
});

describe("placeAlongAxis", () => {
  const rect = { left: 10, top: 20, width: 40, height: 30 };

  it("uses X midpoint for the default horizontal strip", () => {
    expect(placeAlongAxis("x", 29, 999, rect)).toBe("before");
    expect(placeAlongAxis("x", 30, 999, rect)).toBe("after");
  });

  it("uses Y midpoint for stacked rows", () => {
    expect(placeAlongAxis("y", 999, 34, rect)).toBe("before");
    expect(placeAlongAxis("y", 999, 35, rect)).toBe("after");
  });
});

function mockRect(
  el: Element,
  box: { left?: number; top?: number; width?: number; height?: number },
) {
  const left = box.left ?? 0;
  const top = box.top ?? 0;
  const width = box.width ?? 40;
  const height = box.height ?? 20;
  vi.spyOn(el, "getBoundingClientRect").mockReturnValue({
    x: left,
    y: top,
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
    toJSON() {},
  });
}

function SortableHarness({
  initial,
  onReorder,
  onSelect,
}: {
  initial: string[];
  onReorder?: (ids: string[]) => void;
  onSelect?: (id: string) => void;
}) {
  const [ids, setIds] = useState(initial);
  const { getItemProps, draggingId } = useSortableTabIds(ids, (next) => {
    setIds(next);
    onReorder?.(next);
  });
  return (
    <HorizontalTabStrip aria-label="测试标签">
      {ids.map((id) => (
        <SortableTab key={id} id={id} getItemProps={getItemProps}>
          <button type="button" onClick={() => onSelect?.(id)}>
            {id}
          </button>
          <button type="button" data-no-tab-drag aria-label={`关闭 ${id}`}>
            x
          </button>
          <span data-testid={`drag-${id}`}>
            {draggingId === id ? "dragging" : "idle"}
          </span>
        </SortableTab>
      ))}
    </HorizontalTabStrip>
  );
}

describe("HorizontalTabStrip / useSortableTabIds", () => {
  it("renders the strip landmark", () => {
    render(
      <HorizontalTabStrip>
        <div>tab</div>
      </HorizontalTabStrip>,
    );
    expect(screen.getByRole("navigation", { name: "标签页" })).toBeTruthy();
  });

  // Compact tab chrome uses fade arrows, not a native overlay bar. Opt out via
  // the unlayered `.scrollbar-hidden` class — Tailwind's `[scrollbar-width:none]`
  // sits in @layer utilities and silently loses to unlayered globals.
  it("opts the scroll container out of the native overlay scrollbar", () => {
    const { container } = render(
      <HorizontalTabStrip>
        <div>tab</div>
      </HorizontalTabStrip>,
    );
    const scroller = container.querySelector(".overflow-x-auto");
    expect(scroller?.classList.contains("scrollbar-hidden")).toBe(true);
  });

  it("does not start a drag from data-no-tab-drag chrome", () => {
    const onReorder = vi.fn();
    render(<SortableHarness initial={["a", "b"]} onReorder={onReorder} />);
    const close = screen.getByLabelText("关闭 a");
    fireEvent.pointerDown(close, {
      button: 0,
      clientX: 10,
      clientY: 10,
      pointerId: 1,
    });
    fireEvent.pointerMove(document, {
      clientX: 40,
      clientY: 10,
      pointerId: 1,
    });
    expect(screen.getByTestId("drag-a").textContent).toBe("idle");
    expect(onReorder).not.toHaveBeenCalled();
  });

  it("activates via child button click without dragging", () => {
    const onSelect = vi.fn();
    render(<SortableHarness initial={["a", "b"]} onSelect={onSelect} />);
    const tab = screen.getByRole("button", { name: "a" });
    fireEvent.pointerDown(tab, {
      button: 0,
      clientX: 10,
      clientY: 10,
      pointerId: 1,
    });
    fireEvent.pointerUp(document, {
      clientX: 10,
      clientY: 10,
      pointerId: 1,
    });
    fireEvent.click(tab);
    expect(onSelect).toHaveBeenCalledWith("a");
    expect(screen.getByTestId("drag-a").textContent).toBe("idle");
  });

  it("suppresses the trailing click after a drag past threshold", () => {
    const onSelect = vi.fn();
    const setCapture = vi.spyOn(Element.prototype, "setPointerCapture");
    render(<SortableHarness initial={["a", "b"]} onSelect={onSelect} />);
    const tab = screen.getByRole("button", { name: "a" });
    fireEvent.pointerDown(tab, {
      button: 0,
      clientX: 10,
      clientY: 10,
      pointerId: 1,
    });
    fireEvent.pointerMove(document, {
      clientX: 10 + TAB_DRAG_THRESHOLD_PX + 1,
      clientY: 10,
      pointerId: 1,
    });
    expect(screen.getByTestId("drag-a").textContent).toBe("dragging");
    expect(setCapture).toHaveBeenCalled();
    fireEvent.pointerUp(document, {
      clientX: 10 + TAB_DRAG_THRESHOLD_PX + 1,
      clientY: 10,
      pointerId: 1,
    });
    fireEvent.click(tab);
    expect(onSelect).not.toHaveBeenCalled();
    setCapture.mockRestore();
  });

  it("idle tabs show grab cursor; a completed drag reorders along X", () => {
    const onReorder = vi.fn();
    render(<SortableHarness initial={["a", "b"]} onReorder={onReorder} />);
    const a = screen.getByTestId("drag-a").closest("[data-tab-id]");
    const b = screen.getByTestId("drag-b").closest("[data-tab-id]");
    expect(a).toBeTruthy();
    expect(b).toBeTruthy();
    expect(a?.classList.contains("cursor-grab")).toBe(true);
    mockRect(a as Element, { left: 0, width: 40, height: 20 });
    mockRect(b as Element, { left: 40, width: 40, height: 20 });
    document.elementsFromPoint = () => [b as Element];
    fireEvent.pointerDown(a as Element, {
      button: 0,
      clientX: 10,
      clientY: 10,
      pointerId: 1,
    });
    fireEvent.pointerMove(document, {
      clientX: 10 + TAB_DRAG_THRESHOLD_PX + 1,
      clientY: 10,
      pointerId: 1,
    });
    fireEvent.pointerMove(document, {
      clientX: 70,
      clientY: 10,
      pointerId: 1,
    });
    fireEvent.pointerUp(document, {
      clientX: 70,
      clientY: 10,
      pointerId: 1,
    });
    expect(onReorder).toHaveBeenCalledWith(["b", "a"]);
  });
});

function VerticalHarness({
  initial,
  onReorder,
}: {
  initial: string[];
  onReorder?: (ids: string[]) => void;
}) {
  const [ids, setIds] = useState(initial);
  const { getItemProps, draggingId, overId, place } = useSortableTabIds(
    ids,
    (next) => {
      setIds(next);
      onReorder?.(next);
    },
    { axis: "y", idleGrabCursor: false },
  );
  return (
    <div>
      {ids.map((id) => (
        <div key={id} {...getItemProps(id)}>
          <span data-testid={`drag-${id}`}>
            {draggingId === id ? "dragging" : "idle"}
          </span>
          <span data-testid={`over-${id}`}>
            {overId === id ? (place ?? "") : ""}
          </span>
          {id}
        </div>
      ))}
    </div>
  );
}

describe("useSortableTabIds axis y", () => {
  it("does not put grab cursor on idle stacked rows", () => {
    render(<VerticalHarness initial={["a", "b"]} />);
    const row = screen.getByTestId("drag-a").closest("[data-tab-id]");
    expect(row?.classList.contains("cursor-grab")).toBe(false);
  });

  it("reorders along Y after threshold", () => {
    const onReorder = vi.fn();
    render(<VerticalHarness initial={["a", "b"]} onReorder={onReorder} />);
    const a = screen.getByTestId("drag-a").closest("[data-tab-id]");
    const b = screen.getByTestId("drag-b").closest("[data-tab-id]");
    expect(a).toBeTruthy();
    expect(b).toBeTruthy();
    mockRect(a as Element, { top: 0, height: 32, width: 200 });
    mockRect(b as Element, { top: 40, height: 32, width: 200 });
    document.elementsFromPoint = () => [b as Element];
    fireEvent.pointerDown(a as Element, {
      button: 0,
      clientX: 10,
      clientY: 8,
      pointerId: 1,
    });
    fireEvent.pointerMove(document, {
      clientX: 10,
      clientY: 8 + TAB_DRAG_THRESHOLD_PX + 1,
      pointerId: 1,
    });
    fireEvent.pointerMove(document, {
      clientX: 10,
      clientY: 64,
      pointerId: 1,
    });
    expect(screen.getByTestId("over-b").textContent).toBe("after");
    fireEvent.pointerUp(document, {
      clientX: 10,
      clientY: 64,
      pointerId: 1,
    });
    expect(onReorder).toHaveBeenCalledWith(["b", "a"]);
  });
});
