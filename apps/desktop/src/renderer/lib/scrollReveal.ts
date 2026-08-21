/** Auto-hide scrollbars on engines that don't have OS overlay bars (Firefox).
 * Chromium/Electron uses native overlay (per-pane, over content) and ignores
 * `.is-scrolling`; this listener still flags the element that actually
 * scrolled — never `<html>` plus descendants, which used to light up every pane.
 *
 * Capture-phase: `scroll` does not bubble. Passive: we never preventDefault.
 */
const SCROLLING_CLASS = "is-scrolling";
const HIDE_DELAY_MS = 900;

const hideTimers = new WeakMap<Element, number>();
let installed = false;

function scrollingElementOf(event: Event): Element | null {
  const target = event.target;
  if (target === document || target === window) {
    return document.scrollingElement ?? document.documentElement;
  }
  if (target instanceof Element) return target;
  return null;
}

export function initScrollReveal(): void {
  if (installed || typeof document === "undefined") return;
  installed = true;

  const onScroll = (event: Event) => {
    const el = scrollingElementOf(event);
    if (!el) return;
    el.classList.add(SCROLLING_CLASS);
    const prev = hideTimers.get(el);
    if (prev !== undefined) window.clearTimeout(prev);
    hideTimers.set(
      el,
      window.setTimeout(() => {
        el.classList.remove(SCROLLING_CLASS);
        hideTimers.delete(el);
      }, HIDE_DELAY_MS),
    );
  };

  window.addEventListener("scroll", onScroll, { capture: true, passive: true });
}
