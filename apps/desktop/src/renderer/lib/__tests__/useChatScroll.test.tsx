// @vitest-environment jsdom
import { useChatScroll } from "@/lib/useChatScroll";
import type { Message } from "@/stores/conversation";
import { act, render } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type RoCallback = ResizeObserverCallback;

let roInstances: Array<{
  callback: RoCallback;
  observe: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
}> = [];

function installResizeObserverMock() {
  roInstances = [];
  globalThis.ResizeObserver = class {
    callback: RoCallback;
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
    constructor(cb: RoCallback) {
      this.callback = cb;
      roInstances.push(this);
    }
  } as unknown as typeof ResizeObserver;
}

function fireContentResize() {
  const instance = roInstances[roInstances.length - 1];
  if (!instance) throw new Error("no ResizeObserver");
  const entry = {
    target: document.createElement("div"),
    contentRect: {} as DOMRectReadOnly,
    borderBoxSize: [],
    contentBoxSize: [],
    devicePixelContentBoxSize: [],
  } as unknown as ResizeObserverEntry;
  act(() => {
    instance.callback([entry], instance as unknown as ResizeObserver);
  });
}

function stubHeights(
  el: HTMLElement,
  opts: { scrollHeight: number; clientHeight?: number; scrollTop?: number },
) {
  let scrollHeight = opts.scrollHeight;
  Object.defineProperty(el, "scrollHeight", {
    configurable: true,
    get: () => scrollHeight,
    set: (v: number) => {
      scrollHeight = v;
    },
  });
  Object.defineProperty(el, "clientHeight", {
    configurable: true,
    value: opts.clientHeight ?? 200,
  });
  if (opts.scrollTop != null) el.scrollTop = opts.scrollTop;
}

const MSG: Message = {
  id: "m1",
  role: "user",
  content: "hi",
  createdAt: "2026-01-01T00:00:00Z",
  executionId: null,
  isStreaming: false,
};

type ChatApi = ReturnType<typeof useChatScroll>;

function ChatHarness({
  messages,
  resetKey,
  contentKey,
  hasMoreAfter = false,
  onReady,
}: {
  messages: Message[];
  resetKey: string | null;
  contentKey: string;
  hasMoreAfter?: boolean;
  onReady: (api: ChatApi, scroll: HTMLElement) => void;
}) {
  const api = useChatScroll({
    firstMessageId: messages[0]?.id ?? null,
    hasTranscript: messages.length > 0,
    resetKey,
    contentKey,
    hasMoreBefore: false,
    hasMoreAfter,
    loadingOlder: false,
    loadingNewer: false,
    onLoadOlder: () => {},
    onLoadNewer: () => {},
    onJumpToLatest: () => {},
  });
  useEffect(() => {
    const scroll = api.scrollRef.current;
    if (scroll) onReady(api, scroll);
  });
  return (
    <div ref={api.scrollRef} data-testid="scroll">
      {messages.length > 0 && (
        <div ref={api.contentRef} data-testid="content" />
      )}
    </div>
  );
}

function requireReady(
  api: ChatApi | null,
  scrollEl: HTMLElement | null,
): { api: ChatApi; scrollEl: HTMLElement } {
  if (!api || !scrollEl) throw new Error("ChatHarness did not mount");
  return { api, scrollEl };
}

describe("useChatScroll layout follow", () => {
  beforeEach(() => {
    installResizeObserverMock();
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      cb(0);
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", () => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("follows async height growth while stuck", () => {
    const box: { api: ChatApi | null; scroll: HTMLElement | null } = {
      api: null,
      scroll: null,
    };

    render(
      <ChatHarness
        messages={[MSG]}
        resetKey="chat-1"
        contentKey="m1-2-0"
        onReady={(a, scroll) => {
          box.api = a;
          box.scroll = scroll;
        }}
      />,
    );

    const ready = requireReady(box.api, box.scroll);
    expect(roInstances.length).toBeGreaterThan(0);
    expect(roInstances[roInstances.length - 1]?.observe).toHaveBeenCalledTimes(
      2,
    );

    stubHeights(ready.scrollEl, { scrollHeight: 400, scrollTop: 200 });
    stubHeights(ready.scrollEl, {
      scrollHeight: 900,
      scrollTop: ready.scrollEl.scrollTop,
    });
    fireContentResize();

    expect(ready.scrollEl.scrollTop).toBe(900);
    expect(box.api?.atBottom).toBe(true);
  });

  it("does not follow height growth after detach", () => {
    const box: { api: ChatApi | null; scroll: HTMLElement | null } = {
      api: null,
      scroll: null,
    };

    render(
      <ChatHarness
        messages={[MSG]}
        resetKey="chat-1"
        contentKey="m1-2-0"
        onReady={(a, scroll) => {
          box.api = a;
          box.scroll = scroll;
        }}
      />,
    );

    const ready = requireReady(box.api, box.scroll);
    stubHeights(ready.scrollEl, { scrollHeight: 400, scrollTop: 200 });

    act(() => {
      ready.scrollEl.dispatchEvent(new WheelEvent("wheel", { deltaY: -40 }));
    });
    expect(box.api?.atBottom).toBe(false);
    const topBefore = ready.scrollEl.scrollTop;

    stubHeights(ready.scrollEl, {
      scrollHeight: 900,
      scrollTop: ready.scrollEl.scrollTop,
    });
    fireContentResize();

    expect(ready.scrollEl.scrollTop).toBe(topBefore);
    expect(box.api?.atBottom).toBe(false);
  });

  it("does not follow height growth in a historical window", () => {
    const box: { api: ChatApi | null; scroll: HTMLElement | null } = {
      api: null,
      scroll: null,
    };

    render(
      <ChatHarness
        messages={[MSG]}
        resetKey="chat-1"
        contentKey="m1-2-0"
        hasMoreAfter
        onReady={(a, scroll) => {
          box.api = a;
          box.scroll = scroll;
        }}
      />,
    );

    const ready = requireReady(box.api, box.scroll);
    stubHeights(ready.scrollEl, { scrollHeight: 400, scrollTop: 120 });
    const topBefore = ready.scrollEl.scrollTop;

    stubHeights(ready.scrollEl, {
      scrollHeight: 900,
      scrollTop: ready.scrollEl.scrollTop,
    });
    fireContentResize();

    expect(ready.scrollEl.scrollTop).toBe(topBefore);
    expect(box.api?.atBottom).toBe(false);
  });

  it("starts observing after empty transcript gains messages", () => {
    const box: { api: ChatApi | null; scroll: HTMLElement | null } = {
      api: null,
      scroll: null,
    };

    const { rerender } = render(
      <ChatHarness
        messages={[]}
        resetKey="chat-1"
        contentKey=""
        onReady={(a, scroll) => {
          box.api = a;
          box.scroll = scroll;
        }}
      />,
    );

    expect(roInstances).toHaveLength(0);

    rerender(
      <ChatHarness
        messages={[MSG]}
        resetKey="chat-1"
        contentKey="m1-2-0"
        onReady={(a, scroll) => {
          box.api = a;
          box.scroll = scroll;
        }}
      />,
    );

    const ready = requireReady(box.api, box.scroll);
    expect(roInstances.length).toBeGreaterThan(0);

    stubHeights(ready.scrollEl, { scrollHeight: 400, scrollTop: 200 });
    stubHeights(ready.scrollEl, {
      scrollHeight: 900,
      scrollTop: ready.scrollEl.scrollTop,
    });
    fireContentResize();

    expect(ready.scrollEl.scrollTop).toBe(900);
    expect(box.api?.atBottom).toBe(true);
  });

  it("re-pins on contentKey change while stuck (settle / process fold shrink)", () => {
    const box: { api: ChatApi | null; scroll: HTMLElement | null } = {
      api: null,
      scroll: null,
    };

    const { rerender } = render(
      <ChatHarness
        messages={[MSG]}
        resetKey="chat-1"
        contentKey="m1-10-0-1"
        onReady={(a, scroll) => {
          box.api = a;
          box.scroll = scroll;
        }}
      />,
    );

    const ready = requireReady(box.api, box.scroll);
    stubHeights(ready.scrollEl, { scrollHeight: 2000, scrollTop: 1800 });
    // Same commit as isStreaming→false: bubble shrinks, then layout-effect pin.
    stubHeights(ready.scrollEl, {
      scrollHeight: 400,
      scrollTop: ready.scrollEl.scrollTop,
    });
    rerender(
      <ChatHarness
        messages={[MSG]}
        resetKey="chat-1"
        contentKey="m1-10-0-0"
        onReady={(a, scroll) => {
          box.api = a;
          box.scroll = scroll;
        }}
      />,
    );

    expect(ready.scrollEl.scrollTop).toBe(400);
    expect(box.api?.atBottom).toBe(true);
  });

  it("does not re-pin on contentKey change after detach", () => {
    const box: { api: ChatApi | null; scroll: HTMLElement | null } = {
      api: null,
      scroll: null,
    };

    const { rerender } = render(
      <ChatHarness
        messages={[MSG]}
        resetKey="chat-1"
        contentKey="m1-10-0-1"
        onReady={(a, scroll) => {
          box.api = a;
          box.scroll = scroll;
        }}
      />,
    );

    const ready = requireReady(box.api, box.scroll);
    stubHeights(ready.scrollEl, { scrollHeight: 2000, scrollTop: 1800 });
    act(() => {
      ready.scrollEl.dispatchEvent(new WheelEvent("wheel", { deltaY: -40 }));
    });
    expect(box.api?.atBottom).toBe(false);
    const topBefore = ready.scrollEl.scrollTop;

    stubHeights(ready.scrollEl, {
      scrollHeight: 400,
      scrollTop: ready.scrollEl.scrollTop,
    });
    rerender(
      <ChatHarness
        messages={[MSG]}
        resetKey="chat-1"
        contentKey="m1-10-0-0"
        onReady={(a, scroll) => {
          box.api = a;
          box.scroll = scroll;
        }}
      />,
    );

    expect(ready.scrollEl.scrollTop).toBe(topBefore);
    expect(box.api?.atBottom).toBe(false);
  });
});
