// @vitest-environment jsdom
import {
  RunDetailList,
  extraStreamingIndexes,
} from "@/components/chat/detail/RunDetailList";
import type { TimelineNode } from "@/lib/processTimeline";
import { cleanup, render, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

afterEach(() => {
  cleanup();
  HTMLElement.prototype.getBoundingClientRect = origGetBoundingClientRect;
});

const origGetBoundingClientRect = HTMLElement.prototype.getBoundingClientRect;

const viewport = {
  height: 240,
  width: 400,
  top: 0,
  left: 0,
  bottom: 240,
  right: 400,
  x: 0,
  y: 0,
  toJSON() {},
} as DOMRect;

beforeEach(() => {
  globalThis.ResizeObserver = class {
    cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) {
      this.cb = cb;
    }
    observe(el: Element) {
      this.cb(
        [
          {
            target: el,
            contentRect: viewport,
          } as ResizeObserverEntry,
        ],
        this as unknown as ResizeObserver,
      );
    }
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;

  HTMLElement.prototype.getBoundingClientRect = function () {
    const index = this.getAttribute("data-index");
    if (index != null) {
      const h = Number(index) === 0 ? 200 : 36;
      return {
        height: h,
        width: 400,
        top: 0,
        left: 0,
        bottom: h,
        right: 400,
        x: 0,
        y: 0,
        toJSON() {},
      };
    }
    return viewport;
  };
});

const nodes: TimelineNode[] = Array.from({ length: 80 }, (_, i) => ({
  kind: "reasoning",
  text: `想 ${i}`,
}));
const nodeKeys = nodes.map((_, i) => `r${i}`);

function Harness() {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  return (
    <div
      ref={(node) => {
        if (node) {
          Object.defineProperty(node, "clientHeight", {
            configurable: true,
            value: 240,
          });
          Object.defineProperty(node, "clientWidth", {
            configurable: true,
            value: 400,
          });
          node.getBoundingClientRect = () => viewport;
        }
        setEl(node);
      }}
      style={{ height: 240, overflow: "auto" }}
    >
      {el ? (
        <RunDetailList
          scrollParent={el}
          head={<div data-testid="task-head">任务</div>}
          foot={<div data-testid="detail-foot">尾</div>}
          nodes={nodes}
          nodeKeys={nodeKeys}
          isStreaming={false}
          renderNode={(node) => (
            <div>{node.kind === "reasoning" ? node.text : null}</div>
          )}
        />
      ) : null}
    </div>
  );
}

describe("extraStreamingIndexes", () => {
  it("pins the last process row and the foot, not count-1 alone", () => {
    expect(extraStreamingIndexes([0, 1, 2], 80, 81)).toEqual([80, 81]);
    expect(extraStreamingIndexes([78, 79, 80, 81], 80, 81)).toEqual([]);
    expect(extraStreamingIndexes([0], -1, 1)).toEqual([1]);
  });
});

describe("RunDetailList inspector windowing", () => {
  it("windows process rows while keeping the task in the same list", async () => {
    const { container } = render(<Harness />);
    await waitFor(() => {
      expect(
        container.querySelector("[data-run-detail-item=head]"),
      ).toBeTruthy();
      const mounted = container.querySelectorAll("[data-timeline-node]").length;
      expect(mounted).toBeGreaterThan(0);
      expect(mounted).toBeLessThan(80);
    });
  });
});
