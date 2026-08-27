import { describe, expect, it } from "vitest";
import {
  hasInlineMarkers,
  inlineToken,
  insertInlineToken,
  migrateLegacyDraft,
  parseInlineBody,
  plainText,
  renderInlineLabels,
  serializeInlineBody,
  stripRange,
} from "../inlineBody";

describe("inlineBody", () => {
  it("roundtrips text and mixed pills", () => {
    const raw = `先看${inlineToken("A", 0)}再改${inlineToken("M", 0)}收工`;
    expect(parseInlineBody(raw)).toEqual([
      { kind: "text", text: "先看" },
      { kind: "attachment", index: 0 },
      { kind: "text", text: "再改" },
      { kind: "mention", index: 0 },
      { kind: "text", text: "收工" },
    ]);
    expect(serializeInlineBody(parseInlineBody(raw))).toBe(raw);
  });

  it("plainText drops markers", () => {
    expect(plainText(`按这个${inlineToken("A", 0)}原则`)).toBe("按这个原则");
    expect(hasInlineMarkers(`x${inlineToken("A", 0)}`)).toBe(true);
    expect(hasInlineMarkers("按这个原则")).toBe(false);
  });

  it("migrates legacy chip-tray drafts once, at the end", () => {
    expect(migrateLegacyDraft("hello", 2, 1)).toBe(
      `hello${inlineToken("A", 0)}${inlineToken("A", 1)}${inlineToken("M", 0)}`,
    );
    const already = `hello${inlineToken("A", 0)}`;
    expect(migrateLegacyDraft(already, 2, 0)).toBe(already);
  });

  it("renderInlineLabels keeps order without file bodies", () => {
    const raw = `按这个${inlineToken("A", 0)}请${inlineToken("M", 0)}看`;
    expect(
      renderInlineLabels(
        raw,
        [{ name: "现行信息.md", kind: "file" }],
        [{ role: "研究员" }],
      ),
    ).toBe("按这个[文件 现行信息.md]请[点名 研究员]看");
  });

  it("inserts and strips in caret space", () => {
    const ins = insertInlineToken("ab", 1, "A", 0);
    expect(ins.value).toBe(`a${inlineToken("A", 0)}b`);
    const stripped = stripRange(ins.value, 1, 1 + inlineToken("A", 0).length);
    expect(stripped.value).toBe("ab");
    expect(stripped.caret).toBe(1);
  });
});
