import iconv from "iconv-lite";
import type { FsEncoding } from "@shared/ipc-contract";

/**
 * Shared decode/encode for workspace text: UTF-8 (BOM), UTF-8, then GB18030.
 * Panel `gbk` is the IPC name for that legacy Chinese encoding.
 */
export function decodeStoredText(
  buf: Buffer,
): { encoding: FsEncoding; text: string } | null {
  if (buf.includes(0)) return null;
  if (buf.length >= 3 && buf[0] === 0xef && buf[1] === 0xbb && buf[2] === 0xbf) {
    return {
      encoding: "utf-8-bom",
      text: new TextDecoder("utf-8").decode(buf.subarray(3)),
    };
  }
  try {
    return {
      encoding: "utf-8",
      text: new TextDecoder("utf-8", { fatal: true }).decode(buf),
    };
  } catch {
    return { encoding: "gbk", text: iconv.decode(buf, "gb18030") };
  }
}

export function encodeStoredText(text: string, encoding: FsEncoding): Buffer {
  if (encoding === "utf-8-bom") {
    return Buffer.concat([
      Buffer.from([0xef, 0xbb, 0xbf]),
      Buffer.from(text, "utf-8"),
    ]);
  }
  if (encoding === "gbk") {
    return iconv.encode(text, "gb18030");
  }
  return Buffer.from(text, "utf-8");
}
