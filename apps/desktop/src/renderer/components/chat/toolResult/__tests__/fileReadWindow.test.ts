import { describe, expect, it } from "vitest";
import {
  isPartialFileReadWindow,
  parseFileReadWindow,
  stripFileReadFooter,
} from "../fileReadWindow";

describe("parseFileReadWindow", () => {
  it("parses a full-file footer", () => {
    expect(parseFileReadWindow("body\n\n（全文 242 行）")).toEqual({
      start: 1,
      end: 242,
      total: 242,
    });
  });

  it("parses a window footer with an en-dash", () => {
    expect(parseFileReadWindow("191| x\n\n（第 1–200 行，共 242 行）")).toEqual(
      { start: 1, end: 200, total: 242 },
    );
  });

  it("parses a requested window that has not hit a safety cap", () => {
    expect(
      parseFileReadWindow(
        "191| x\n\n（第 1–200 行，共 242 行；未达安全顶，省略 limit 可整读）",
      ),
    ).toEqual({ start: 1, end: 200, total: 242 });
  });

  it("parses a cap suffix and an ASCII hyphen", () => {
    expect(
      parseFileReadWindow("x\n\n（第 1-200 行，共 242 行；已达行顶）"),
    ).toEqual({ start: 1, end: 200, total: 242 });
  });

  it("finds the window footer when a PDF page HOW follows", () => {
    expect(
      parseFileReadWindow(
        "body\n\n（第 1–200 行，共 500 行）\n\n抽取第 1–3 页，共 12 页。后面的页请用 start_page=4 再读",
      ),
    ).toEqual({ start: 1, end: 200, total: 500 });
  });

  it("is null when there is no footer", () => {
    expect(parseFileReadWindow("just the file")).toBeNull();
  });
});

describe("isPartialFileReadWindow", () => {
  it("is false for a complete file", () => {
    expect(isPartialFileReadWindow({ start: 1, end: 242, total: 242 })).toBe(
      false,
    );
  });

  it("is true for a truncated window", () => {
    expect(isPartialFileReadWindow({ start: 1, end: 200, total: 242 })).toBe(
      true,
    );
  });
});

describe("stripFileReadFooter", () => {
  it("removes a window footer and keeps the body", () => {
    expect(stripFileReadFooter("191| x\n\n（第 1–200 行，共 242 行）")).toBe(
      "191| x",
    );
  });

  it("removes a requested-window footer", () => {
    expect(
      stripFileReadFooter(
        "191| x\n\n（第 1–200 行，共 242 行；未达安全顶，省略 limit 可整读）",
      ),
    ).toBe("191| x");
  });

  it("removes a full-file footer", () => {
    expect(stripFileReadFooter("hello\n\n（全文 12 行）")).toBe("hello");
  });

  it("keeps a following PDF page HOW", () => {
    expect(
      stripFileReadFooter(
        "body\n\n（第 1–200 行，共 500 行）\n\n抽取第 1–3 页，共 12 页。后面的页请用 start_page=4 再读",
      ),
    ).toBe("body\n\n抽取第 1–3 页，共 12 页。后面的页请用 start_page=4 再读");
  });
});
