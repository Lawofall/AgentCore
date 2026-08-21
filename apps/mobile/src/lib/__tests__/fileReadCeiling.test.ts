import { describe, expect, it } from "vitest";
import { isFileReadCeilingGuidance } from "../fileReadCeiling";

describe("isFileReadCeilingGuidance", () => {
  it("matches same-path ceiling copy on file_read", () => {
    expect(
      isFileReadCeilingGuidance(
        "file_read",
        "已多次读取 `doc.md`（本 run 上限 5 次）。请求范围仍在对话投影窗中，本次不重复灌入全文。",
      ),
    ).toBe(true);
    expect(
      isFileReadCeilingGuidance(
        "file_read",
        "已多次读取 `doc.md`，且上下文中的正文已被清理、再读次数已用尽。",
      ),
    ).toBe(true);
  });

  it("rejects real IO failures and other tools", () => {
    expect(
      isFileReadCeilingGuidance("file_read", "读取文件失败：文件不存在"),
    ).toBe(false);
    expect(
      isFileReadCeilingGuidance(
        "file_write",
        "已多次读取 `doc.md`，勿再读此文件",
      ),
    ).toBe(false);
  });
});
