import iconv from "iconv-lite";
import { describe, expect, it } from "vitest";
import { decodeStoredText, encodeStoredText } from "../textCodec";

describe("textCodec", () => {
  it("round-trips utf-8 and utf-8-bom", () => {
    expect(decodeStoredText(Buffer.from("hi", "utf-8"))).toEqual({
      encoding: "utf-8",
      text: "hi",
    });
    const bom = encodeStoredText("hi", "utf-8-bom");
    expect(bom.subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf]))).toBe(
      true,
    );
    expect(decodeStoredText(bom)).toEqual({
      encoding: "utf-8-bom",
      text: "hi",
    });
  });

  it("round-trips gb18030 as gbk without flipping to utf-8", () => {
    const raw = iconv.encode("你好世界", "gb18030");
    const decoded = decodeStoredText(raw);
    expect(decoded).toEqual({ encoding: "gbk", text: "你好世界" });
    if (!decoded) throw new Error("expected gbk decode");
    const written = encodeStoredText(decoded.text, decoded.encoding);
    expect(written.equals(raw)).toBe(true);
    expect(written.equals(Buffer.from("你好世界", "utf-8"))).toBe(false);
  });

  it("treats NUL as binary", () => {
    expect(decodeStoredText(Buffer.from([0x61, 0x00, 0x62]))).toBeNull();
  });

  it("decodes a GBK prefix the way panel preview slices the file", () => {
    const raw = iconv.encode("你好世界", "gb18030");
    const prefix = raw.subarray(0, 4);
    const decoded = decodeStoredText(prefix);
    expect(decoded?.encoding).toBe("gbk");
    expect(decoded?.text.startsWith("你")).toBe(true);
  });
});
