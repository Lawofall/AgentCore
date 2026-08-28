/**
 * @vitest-environment jsdom
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  COPY_PLAIN_ATTR,
  clipPlainPaste,
  handlePlainCopy,
  handlePlainPaste,
  htmlToPlain,
  normalizeClipboardPlain,
  plainTextFromClipboard,
  plainToUnstyledHtml,
  selectionPlainCopyIsland,
} from "../clipboardPlain";

afterEach(() => {
  document.body.innerHTML = "";
  window.getSelection()?.removeAllRanges();
});

function selectContents(el: Node) {
  const range = document.createRange();
  range.selectNodeContents(el);
  const sel = window.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
}

function clipboardMock() {
  const store: Record<string, string> = {};
  return {
    store,
    getData: (type: string) => store[type] ?? "",
    setData: (type: string, value: string) => {
      store[type] = value;
    },
  };
}

describe("plain clipboard flavors", () => {
  it("normalizes CRLF and strips object-replacement chars", () => {
    expect(normalizeClipboardPlain("a\r\nb\r\uFFFC c")).toBe("a\nb\n c");
  });

  it("prefers text/plain over styled HTML", () => {
    const data = clipboardMock();
    data.setData("text/plain", "hello");
    data.setData(
      "text/html",
      '<div style="background:#e8e8e8;border-radius:12px">hello</div>',
    );
    expect(plainTextFromClipboard(data as unknown as DataTransfer)).toBe(
      "hello",
    );
  });

  it("falls back to stripping HTML when plain is empty", () => {
    expect(
      htmlToPlain(
        '<div class="bg-muted" style="background:rgb(240,240,240)">hi</div>',
      ),
    ).toBe("hi");
  });

  it("unstyled HTML has no background wrapper", () => {
    const html = plainToUnstyledHtml("a <b>\nline");
    expect(html).toContain("a &lt;b&gt;<br>line");
    expect(html).not.toMatch(/background|bg-muted|rounded/i);
  });

  it("clips paste to remaining room including replaced selection", () => {
    expect(
      clipPlainPaste("hello", {
        maxLength: 5,
        currentLength: 3,
        selectedLength: 0,
      }),
    ).toBe("he");
    expect(
      clipPlainPaste("hello", {
        maxLength: 5,
        currentLength: 5,
        selectedLength: 2,
      }),
    ).toBe("he");
  });
});

describe("plain copy islands", () => {
  it("writes plain + unstyled html inside a marked island", () => {
    document.body.innerHTML = `<div ${COPY_PLAIN_ATTR}><p>帮我调研</p></div>`;
    const p = document.querySelector("p");
    if (!p) throw new Error("missing p");
    selectContents(p);
    expect(selectionPlainCopyIsland()).toBeTruthy();

    const data = clipboardMock();
    const preventDefault = vi.fn();
    expect(
      handlePlainCopy({
        preventDefault,
        defaultPrevented: false,
        clipboardData: data as unknown as DataTransfer,
      }),
    ).toBe(true);
    expect(preventDefault).toHaveBeenCalled();
    expect(data.store["text/plain"]).toBe("帮我调研");
    expect(data.store["text/html"]).toBe("帮我调研");
    expect(data.store["text/html"]).not.toMatch(/background|muted/i);
  });

  it("does not intercept unmarked (assistant) selection", () => {
    document.body.innerHTML = `<div class="assistant"><p>答案</p></div>`;
    const p = document.querySelector("p");
    if (!p) throw new Error("missing p");
    selectContents(p);
    expect(selectionPlainCopyIsland()).toBeNull();
    const preventDefault = vi.fn();
    expect(
      handlePlainCopy({
        preventDefault,
        defaultPrevented: false,
        clipboardData: clipboardMock() as unknown as DataTransfer,
      }),
    ).toBe(false);
    expect(preventDefault).not.toHaveBeenCalled();
  });

  it("does not intercept a selection that spans two islands", () => {
    document.body.innerHTML = `
      <div ${COPY_PLAIN_ATTR}><p id="a">one</p></div>
      <div ${COPY_PLAIN_ATTR}><p id="b">two</p></div>`;
    const a = document.getElementById("a");
    const b = document.getElementById("b");
    if (!a?.firstChild || !b?.firstChild) throw new Error("missing text");
    const range = document.createRange();
    range.setStart(a.firstChild, 0);
    range.setEnd(b.firstChild, 3);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    expect(selectionPlainCopyIsland()).toBeNull();
  });
});

function stubInsertText() {
  const insert = vi.fn().mockReturnValue(true);
  Object.defineProperty(document, "execCommand", {
    configurable: true,
    writable: true,
    value: insert,
  });
  return insert;
}

describe("plain paste", () => {
  it("prevents default and does not insert when already handled (files)", () => {
    const insert = stubInsertText();
    expect(
      handlePlainPaste({
        defaultPrevented: true,
        preventDefault: vi.fn(),
        clipboardData: null,
      }),
    ).toBe(false);
    expect(insert).not.toHaveBeenCalled();
  });

  it("inserts stripped text from a styled HTML clipboard", () => {
    const insert = stubInsertText();
    const data = clipboardMock();
    data.setData(
      "text/html",
      '<div style="background:#e8e8e8;padding:12px;border-radius:12px">讨论优化</div>',
    );
    const preventDefault = vi.fn();
    expect(
      handlePlainPaste({
        defaultPrevented: false,
        preventDefault,
        clipboardData: data as unknown as DataTransfer,
      }),
    ).toBe(true);
    expect(preventDefault).toHaveBeenCalled();
    expect(insert).toHaveBeenCalledWith("insertText", false, "讨论优化");
  });
});
