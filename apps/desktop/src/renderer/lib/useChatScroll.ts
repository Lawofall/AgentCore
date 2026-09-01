import {
  distanceFromBottom,
  isScrollUpTouch,
  isScrollUpWheel,
  nextStickState,
} from "@/lib/stickScroll";
import { useActiveStickContentKey } from "@/stores/conversation";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

/**
 * Scroll controller for the AI 对话 transcript: stick-to-bottom *and*
 * bidirectional infinite scroll over a cursor window (载入模型 B).
 *
 * The transcript holds a contiguous window of the conversation, not the whole
 * history. Three concerns are folded into the one scroll container:
 *
 * - **Stick to bottom** while the user is at the live head, so a streaming turn
 *   stays in view — but only when the window actually reaches the tail
 *   (`hasMoreAfter === false`). Upward wheel/touch detaches immediately so the
 *   stream cannot yank the viewport back; hysteresis governs position-based
 *   re-attach. In a historical window sticking is disabled. Async layout growth
 *   (images / Markdown / process timelines) is followed via ResizeObserver on
 *   `contentRef` and the viewport, batched on rAF — same path as
 *   {@link useStickToBottom}. Follow writes `scrollTop` directly so CSS
 *   `scroll-behavior` cannot animate and lose the pin. Observation re-binds
 *   when the transcript wrapper appears (empty → first message).
 * - **Load older on scroll-up**: near the top, fetch the previous page and
 *   prepend it; the inflated top is anchored so the viewport stays on the same
 *   line instead of jumping.
 * - **Load newer on scroll-down**: near the bottom of a historical window, fetch
 *   the next page and append it (no anchoring needed — content added below the
 *   fold doesn't move what's above).
 *
 * `onLoadOlder` / `onLoadNewer` must be idempotent under a burst of scroll events
 * (the service guards with the loading flags); this hook additionally locks the
 * prepend anchor so it won't fire a second older-page before the first lands.
 *
 * The IM 消息 thread keeps using the simpler {@link useStickToBottom} — it is
 * append-only and needs none of the windowing here.
 */

/** Fetch the previous page once the user scrolls within this of the top. */
const TOP_LOAD_THRESHOLD_PX = 240;
/** Fetch the next page once within this of a historical window's bottom. */
const BOTTOM_LOAD_THRESHOLD_PX = 240;

interface ChatScrollOptions {
  firstMessageId: string | null;
  hasTranscript: boolean;
  /** Conversation id — a change re-sticks to the latest item. */
  resetKey: string | null;
  /** Test override; live ChatView uses {@link useActiveStickContentKey}. */
  contentKey?: string;
  hasMoreBefore: boolean;
  hasMoreAfter: boolean;
  loadingOlder: boolean;
  loadingNewer: boolean;
  /** Fetch + prepend the previous page (stable identity). */
  onLoadOlder: () => void;
  /** Fetch + append the next page (stable identity). */
  onLoadNewer: () => void;
  /** Reload the latest window — the 回到底部 action when reading history. */
  onJumpToLatest: () => void;
}

export function useChatScroll(opts: ChatScrollOptions) {
  const { firstMessageId, resetKey, loadingOlder } = opts;
  const storeContentKey = useActiveStickContentKey();
  const contentKey = opts.contentKey ?? storeContentKey;
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const touchYRef = useRef<number | null>(null);
  const [atBottom, setAtBottom] = useState(true);
  const hasTranscript = opts.hasTranscript;

  // Latest props for the scroll listener, so it subscribes once yet always reads
  // current flags/callbacks (avoids re-binding the listener on every toggle).
  const liveRef = useRef(opts);
  liveRef.current = opts;

  // Prepend anchor: distance bookkeeping captured when an older-page fetch is
  // triggered, restored once the new rows land so the viewport doesn't jump.
  const anchorRef = useRef<{
    firstId: string | null;
    prevHeight: number;
    prevTop: number;
  } | null>(null);

  const firstId = firstMessageId;

  const applyStick = useCallback((stuck: boolean) => {
    stickRef.current = stuck;
    setAtBottom(stuck);
  }, []);

  /** Instant pin — bypasses CSS `scroll-behavior`. */
  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, []);

  const jumpToBottom = useCallback(() => {
    // Reading history (newer pages unloaded): snap back to the live head by
    // reloading the latest window; the content-key effect then lands at bottom.
    if (liveRef.current.hasMoreAfter) {
      applyStick(true);
      liveRef.current.onJumpToLatest();
      return;
    }
    applyStick(true);
    scrollToBottom();
  }, [applyStick, scrollToBottom]);

  // User-driven scroll + upward gesture: toggle stick, page at either edge.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const syncStickFromPosition = () => {
      const live = liveRef.current;
      if (live.hasMoreAfter) {
        applyStick(false);
        return;
      }
      applyStick(nextStickState(stickRef.current, distanceFromBottom(el)));
    };

    const onScroll = () => {
      const live = liveRef.current;
      syncStickFromPosition();

      if (
        el.scrollTop < TOP_LOAD_THRESHOLD_PX &&
        live.hasMoreBefore &&
        !live.loadingOlder &&
        !anchorRef.current
      ) {
        anchorRef.current = {
          firstId: live.firstMessageId,
          prevHeight: el.scrollHeight,
          prevTop: el.scrollTop,
        };
        live.onLoadOlder();
      }

      if (
        distanceFromBottom(el) < BOTTOM_LOAD_THRESHOLD_PX &&
        live.hasMoreAfter &&
        !live.loadingNewer
      ) {
        live.onLoadNewer();
      }
    };

    const onWheel = (e: WheelEvent) => {
      if (
        isScrollUpWheel(e.deltaY) &&
        stickRef.current &&
        !liveRef.current.hasMoreAfter
      ) {
        applyStick(false);
      }
    };

    const onTouchStart = (e: TouchEvent) => {
      touchYRef.current = e.touches[0]?.clientY ?? null;
    };

    const onTouchMove = (e: TouchEvent) => {
      const y = e.touches[0]?.clientY;
      const prev = touchYRef.current;
      if (y == null || prev == null) return;
      if (
        isScrollUpTouch(prev, y) &&
        stickRef.current &&
        !liveRef.current.hasMoreAfter
      ) {
        applyStick(false);
      }
      touchYRef.current = y;
    };

    const onTouchEnd = () => {
      touchYRef.current = null;
    };

    el.addEventListener("scroll", onScroll, { passive: true });
    el.addEventListener("wheel", onWheel, { passive: true });
    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: true });
    el.addEventListener("touchend", onTouchEnd, { passive: true });
    el.addEventListener("touchcancel", onTouchEnd, { passive: true });
    return () => {
      el.removeEventListener("scroll", onScroll);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("touchend", onTouchEnd);
      el.removeEventListener("touchcancel", onTouchEnd);
    };
  }, [applyStick]);

  // Older page landed (or finished with nothing new): restore the anchored
  // position so the inflated top doesn't shift the user's place, then release
  // the lock so the next scroll-up can page again.
  useLayoutEffect(() => {
    const a = anchorRef.current;
    if (!a) return;
    const el = scrollRef.current;
    if (a.firstId !== firstId && el) {
      el.scrollTop = a.prevTop + (el.scrollHeight - a.prevHeight);
      anchorRef.current = null;
    } else if (!loadingOlder) {
      anchorRef.current = null;
    }
  }, [firstId, loadingOlder]);

  // New content at the tail (new turn / streaming tokens): follow only while
  // stuck and at the real head; otherwise just refresh the button state.
  // biome-ignore lint/correctness/useExhaustiveDependencies: contentKey is an intentional re-run key; helpers are stable.
  useLayoutEffect(() => {
    if (stickRef.current && !liveRef.current.hasMoreAfter) {
      scrollToBottom();
    } else {
      // Detached or historical window: keep 回到底部 visible until jump / re-attach.
      setAtBottom(false);
    }
  }, [contentKey, scrollToBottom]);

  // Context switch: re-stick and land on the latest item, dropping any anchor.
  // biome-ignore lint/correctness/useExhaustiveDependencies: resetKey is an intentional re-run key.
  useLayoutEffect(() => {
    applyStick(true);
    anchorRef.current = null;
    scrollToBottom();
  }, [resetKey, applyStick, scrollToBottom]);

  // Content / viewport layout growth (async images, Markdown, process timelines):
  // follow only while stuck at the live head. rAF batches RO deliveries and
  // avoids "ResizeObserver loop" when we mutate scrollTop in the same turn.
  // Re-bind when the transcript wrapper appears (empty → first message); a
  // first-mount-only observe would miss ChatView's conditional content node.
  // biome-ignore lint/correctness/useExhaustiveDependencies: hasTranscript re-binds when the wrapper mounts
  useEffect(() => {
    const content = contentRef.current;
    const viewport = scrollRef.current;
    if (!content) return;

    let raf = 0;
    const followFromLayout = () => {
      raf = 0;
      if (stickRef.current && !liveRef.current.hasMoreAfter) {
        scrollToBottom();
      } else {
        setAtBottom(false);
      }
    };

    const ro = new ResizeObserver(() => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(followFromLayout);
    });
    ro.observe(content);
    if (viewport) ro.observe(viewport);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [scrollToBottom, hasTranscript]);

  return { scrollRef, contentRef, atBottom, jumpToBottom };
}
