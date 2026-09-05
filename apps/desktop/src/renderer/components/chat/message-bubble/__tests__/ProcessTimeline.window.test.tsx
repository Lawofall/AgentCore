// @vitest-environment jsdom
import { ProcessTimeline } from "@/components/chat/message-bubble/ProcessTimeline";
import { ProcessTimelineScrollContext } from "@/components/chat/message-bubble/processTimelineScroll";
import type { ProcessStep } from "@/types/events";
import { cleanup, render, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/disclosure", () => ({
  useStreamAwareDisclosure: () => [true, vi.fn()],
  usePersistentDisclosure: () => [false, vi.fn()],
}));

afterEach(cleanup);

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
});

const process: ProcessStep[] = Array.from({ length: 80 }, (_, i) =>
  i % 2 === 0
    ? {
        kind: "reasoning" as const,
        text: `想 ${i}`,
      }
    : {
        kind: "tool" as const,
        id: `t${i}`,
        tool_name: "file_read",
        arguments: { path: `f${i}.ts` },
        result: "ok",
        status: "success" as const,
      },
);

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
        <ProcessTimelineScrollContext.Provider value={el}>
          <ProcessTimeline
            process={process}
            isStreaming={false}
            citations={[]}
            composingTool={null}
            fallbackContent=""
            conversationId={null}
            checkpoints={[]}
            planReviews={[]}
            collapseProcessSteps={false}
          />
        </ProcessTimelineScrollContext.Provider>
      ) : null}
    </div>
  );
}

describe("ProcessTimeline run-detail windowing", () => {
  it("mounts a viewport slice instead of every process row", async () => {
    const { container } = render(<Harness />);
    await waitFor(() => {
      const mounted = container.querySelectorAll("[data-timeline-node]").length;
      expect(mounted).toBeGreaterThan(0);
      expect(mounted).toBeLessThan(80);
    });
  });
});
