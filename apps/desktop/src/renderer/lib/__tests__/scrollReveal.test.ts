// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("initScrollReveal", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    document.documentElement.className = "";
    document.body.replaceChildren();
  });

  afterEach(() => {
    vi.useRealTimers();
    document.documentElement.className = "";
    document.body.replaceChildren();
  });

  async function load() {
    vi.resetModules();
    return import("../scrollReveal");
  }

  function fireScroll(target: EventTarget) {
    target.dispatchEvent(new Event("scroll", { bubbles: false }));
  }

  it("flags only the element that scrolled, not the document or siblings", async () => {
    const { initScrollReveal } = await load();
    initScrollReveal();
    const a = document.createElement("div");
    const b = document.createElement("div");
    document.body.append(a, b);

    fireScroll(a);

    expect(a.classList.contains("is-scrolling")).toBe(true);
    expect(b.classList.contains("is-scrolling")).toBe(false);
    expect(document.documentElement.classList.contains("is-scrolling")).toBe(
      false,
    );
  });

  it("lets two panes reveal independently", async () => {
    const { initScrollReveal } = await load();
    initScrollReveal();
    const a = document.createElement("div");
    const b = document.createElement("div");
    document.body.append(a, b);

    fireScroll(a);
    fireScroll(b);

    expect(a.classList.contains("is-scrolling")).toBe(true);
    expect(b.classList.contains("is-scrolling")).toBe(true);
    expect(document.documentElement.classList.contains("is-scrolling")).toBe(
      false,
    );
  });

  it("clears the flag after the hide delay", async () => {
    const { initScrollReveal } = await load();
    initScrollReveal();
    const a = document.createElement("div");
    document.body.append(a);

    fireScroll(a);
    vi.advanceTimersByTime(899);
    expect(a.classList.contains("is-scrolling")).toBe(true);
    vi.advanceTimersByTime(1);
    expect(a.classList.contains("is-scrolling")).toBe(false);
  });

  it("maps document scroll onto the root scrolling element", async () => {
    const { initScrollReveal } = await load();
    initScrollReveal();

    fireScroll(document);

    expect(document.documentElement.classList.contains("is-scrolling")).toBe(
      true,
    );
  });
});
