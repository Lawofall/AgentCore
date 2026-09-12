// @vitest-environment jsdom
/**
 * Chrome layout of the whiteboard: compact top bar + shapes menu + zoom cluster +
 * left outline + right property dock. Does not assert pixels; jsdom has no 2D canvas.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { WhiteboardCanvas } from "../WhiteboardCanvas";

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  Element.prototype.scrollIntoView ??= () => {};
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => {};
  Element.prototype.releasePointerCapture ??= () => {};
});

beforeEach(() => {
  globalThis.requestAnimationFrame = (() => 0) as typeof requestAnimationFrame;
  globalThis.cancelAnimationFrame = (() => {}) as typeof cancelAnimationFrame;
  HTMLCanvasElement.prototype.getContext = (() => ({
    measureText: () => ({ width: 0 }),
    save: () => {},
    restore: () => {},
    setTransform: () => {},
    font: "",
  })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WhiteboardCanvas chrome", () => {
  it("puts undo / select / shapes on the top bar, not a vertical tool stack", () => {
    render(
      <WhiteboardCanvas
        initialElements={[]}
        onChange={() => {}}
        className="h-40"
      />,
    );

    expect(screen.getByRole("button", { name: "撤销" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "选择 (V)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "形状" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "抓手 / 平移 (H / 空格)" }),
    ).toBeTruthy();
    expect(screen.getByText("100%")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "矩形 (R)" })).toBeNull();
  });

  it("lists drawing tools inside the shapes menu", async () => {
    render(
      <WhiteboardCanvas
        initialElements={[]}
        onChange={() => {}}
        className="h-40"
      />,
    );

    // Radix opens on pointerdown, not click.
    fireEvent.pointerDown(screen.getByRole("button", { name: "形状" }), {
      button: 0,
    });
    expect(await screen.findByRole("menuitem", { name: /矩形/ })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /便签/ })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /画笔/ })).toBeTruthy();
  });

  it("opens an in-place textarea on double-clicking a sticky", () => {
    render(
      <StrictMode>
        <WhiteboardCanvas
          initialElements={[
            {
              id: "s",
              type: "sticky",
              x: 0,
              y: 0,
              width: 140,
              height: 84,
              text: "hello",
              schemaVersion: 1,
            },
          ]}
          onChange={() => {}}
          className="h-40"
        />
      </StrictMode>,
    );

    const canvas = document.querySelector("canvas");
    expect(canvas).toBeTruthy();
    fireEvent.dblClick(canvas as HTMLCanvasElement, {
      clientX: 10,
      clientY: 10,
    });
    const ta = screen.getByTestId("wb-text-edit") as HTMLTextAreaElement;
    expect(ta.value).toBe("hello");
    expect(screen.getByRole("heading", { name: "hello" })).toBeTruthy();
  });

  it("lists scene elements in the left 画布导航 panel", () => {
    render(
      <WhiteboardCanvas
        initialElements={[
          {
            id: "s",
            type: "sticky",
            x: 0,
            y: 0,
            width: 140,
            height: 84,
            text: "hello",
            schemaVersion: 1,
          },
        ]}
        onChange={() => {}}
        className="h-40"
      />,
    );

    expect(screen.getByRole("heading", { name: "画布导航" })).toBeTruthy();
    const row = screen.getByRole("button", { name: /hello/ });
    fireEvent.click(row);
    expect(row.getAttribute("aria-pressed")).toBe("true");
  });

  it("closes the outline to an edge tab", () => {
    render(
      <WhiteboardCanvas
        initialElements={[]}
        onChange={() => {}}
        className="h-40"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "关闭画布导航" }));
    expect(screen.queryByRole("heading", { name: "画布导航" })).toBeNull();
    expect(screen.getByRole("button", { name: "打开画布导航" })).toBeTruthy();
  });

  it("docks transform + style in a right panel after selecting from the outline", () => {
    render(
      <WhiteboardCanvas
        initialElements={[
          {
            id: "s",
            type: "sticky",
            x: 0,
            y: 0,
            width: 140,
            height: 84,
            text: "hello",
            schemaVersion: 1,
          },
        ]}
        onChange={() => {}}
        className="h-40"
      />,
    );

    expect(screen.queryByText("描边")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /hello/ }));
    expect(screen.getByRole("heading", { name: "hello" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "变换" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "样式" }));
    expect(screen.getByText("描边")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "关闭属性面板" }));
    expect(screen.queryByText("描边")).toBeNull();
    expect(screen.getByRole("button", { name: "打开属性面板" })).toBeTruthy();
  });

  it("hides the property dock when nothing is selected", () => {
    render(
      <WhiteboardCanvas
        initialElements={[]}
        onChange={() => {}}
        className="h-40"
      />,
    );

    expect(screen.queryByRole("button", { name: "打开属性面板" })).toBeNull();
    expect(screen.queryByRole("button", { name: "关闭属性面板" })).toBeNull();
    expect(screen.queryByText("描边")).toBeNull();
  });
});
