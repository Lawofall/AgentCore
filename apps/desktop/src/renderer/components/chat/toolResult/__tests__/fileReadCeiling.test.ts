import { describe, expect, it } from "vitest";
import { isFileReadCeilingGuidance } from "../fileReadCeiling";

describe("isFileReadCeilingGuidance", () => {
  it("matches same-path ceiling copy on file_read", () => {
    expect(
      isFileReadCeilingGuidance(
        "file_read",
        "已多次读取 `doc.md`（本 run 上限 5 次）。请求范围仍在对话投影窗中，本次不重复灌入全文。请直接使用已有正文，勿再读全文。",
      ),
    ).toBe(true);
    expect(
      isFileReadCeilingGuidance(
        "file_read",
        "已多次读取 `doc.md`，且上下文中的正文已被清理、再读次数已用尽。请依据清理摘要推进。",
      ),
    ).toBe(true);
  });

  it("rejects real IO failures and other tools", () => {
    expect(
      isFileReadCeilingGuidance("file_read", "读取文件失败：文件不存在"),
    ).toBe(false);
    expect(
      isFileReadCeilingGuidance("file_read", "路径 'x' 超出了工作区范围。"),
    ).toBe(false);
    expect(
      isFileReadCeilingGuidance(
        "file_write",
        "已多次读取 `doc.md`，勿再读此文件",
      ),
    ).toBe(false);
    expect(isFileReadCeilingGuidance("file_read", null)).toBe(false);
    expect(isFileReadCeilingGuidance("file_read", "")).toBe(false);
  });
});
