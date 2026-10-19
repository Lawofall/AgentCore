// @vitest-environment jsdom

import { ExportMarkdownDialog } from "@/components/files/ExportMarkdownDialog";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

describe("ExportMarkdownDialog", () => {
  it("默认 Word，点选档位即导出", () => {
    const onPick = vi.fn();
    render(
      <ExportMarkdownDialog
        open
        fileName="起诉状.md"
        onOpenChange={vi.fn()}
        onPick={onPick}
      />,
    );
    expect(screen.getByText("同目录生成 起诉状.docx")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /正式文书/ }));
    expect(onPick).toHaveBeenCalledWith("docx", "official");
  });

  it("切到 PDF 后技术报告走 standard", () => {
    const onPick = vi.fn();
    render(
      <ExportMarkdownDialog
        open
        fileName="方案.md"
        onOpenChange={vi.fn()}
        onPick={onPick}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "PDF" }));
    expect(screen.getByText("同目录生成 方案.pdf")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /技术报告/ }));
    expect(onPick).toHaveBeenCalledWith("pdf", "standard");
  });

  it("取消只关窗、不导出", () => {
    const onPick = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <ExportMarkdownDialog
        open
        fileName="方案.md"
        onOpenChange={onOpenChange}
        onPick={onPick}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onPick).not.toHaveBeenCalled();
  });
});
