// @vitest-environment jsdom

import { FilePreviewBody } from "@/components/files/FilePreviewBody";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

describe("FilePreviewBody", () => {
  it("image: contain preview opens lightbox on click; no mime footer or zoom chrome", () => {
    render(
      <FilePreviewBody
        name="shot.png"
        result={{
          kind: "image",
          dataUrl: "data:image/png;base64,AAAA",
          mime: "image/png",
          size: 1280,
        }}
      />,
    );

    expect(screen.queryByText(/image\/png/)).toBeNull();
    expect(screen.queryByRole("button", { name: "放大" })).toBeNull();
    expect(screen.queryByRole("button", { name: "缩小" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "放大预览 shot.png" }));
    expect(screen.getByRole("dialog", { name: "shot.png" })).toBeTruthy();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "shot.png" })).toBeNull();
  });

  it("pdf: iframe only, no mime footer", () => {
    const { container } = render(
      <FilePreviewBody
        name="doc.pdf"
        result={{
          kind: "pdf",
          dataUrl: "data:application/pdf;base64,JVBERg==",
          mime: "application/pdf",
          size: 4096,
        }}
      />,
    );

    const iframe = container.querySelector("iframe");
    expect(iframe).toBeTruthy();
    expect(iframe?.getAttribute("src")).toBe(
      "data:application/pdf;base64,JVBERg==",
    );
    expect(iframe?.getAttribute("title")).toBe("doc.pdf");
    expect(screen.queryByText(/application\/pdf/)).toBeNull();
  });

  it("too-large / binary: title only, no download-or-open restatement", () => {
    const { rerender } = render(
      <FilePreviewBody name="big.bin" result={{ kind: "too-large" }} />,
    );
    expect(screen.getByText("文件过大")).toBeTruthy();
    expect(screen.queryByText(/请下载或用系统默认/)).toBeNull();

    rerender(
      <FilePreviewBody
        name="sheet.xlsx"
        result={{
          kind: "binary",
          mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          size: 2048,
        }}
      />,
    );
    expect(screen.getByText("无法预览此文件")).toBeTruthy();
    expect(screen.queryByText(/无法在面板内预览/)).toBeNull();
  });

  it("过大阈值折进标题；octet-stream 不展示，体积留下", () => {
    const { rerender } = render(
      <FilePreviewBody
        name="huge.png"
        result={{
          kind: "binary",
          mime: "image/png",
          size: 11 * 1024 * 1024,
          reason: "图片过大（超过 10MB）",
        }}
        onDownload={() => undefined}
      />,
    );
    expect(screen.getByText("图片过大（超过 10MB）")).toBeTruthy();
    expect(screen.queryByText(/请下载或用系统默认/)).toBeNull();
    expect(screen.getByRole("button", { name: "下载" })).toBeTruthy();

    rerender(
      <FilePreviewBody
        name="legacy.pdf"
        result={{
          kind: "binary",
          mime: "application/pdf",
          size: 16 * 1024 * 1024,
          reason: "PDF 过大（超过 15MB），请下载或用系统默认程序打开",
        }}
      />,
    );
    expect(screen.getByText("PDF 过大（超过 15MB）")).toBeTruthy();
    expect(screen.queryByText(/请下载或用系统默认/)).toBeNull();

    rerender(
      <FilePreviewBody
        name="blob.bin"
        result={{
          kind: "binary",
          mime: "application/octet-stream",
          size: 12288,
        }}
        onDownload={() => undefined}
      />,
    );
    expect(screen.getByText("无法预览此文件")).toBeTruthy();
    expect(screen.queryByText(/octet-stream/)).toBeNull();
    expect(screen.getByText("12 KB")).toBeTruthy();
  });

  it("兜底面：两条出路都给可点主按钮（binary / too-large 同款）", () => {
    const onOpenWithOsDefaultApp = vi.fn();
    const onDownload = vi.fn();
    const { rerender } = render(
      <FilePreviewBody
        name="sheet.xlsx"
        result={{ kind: "binary", mime: "application/vnd.ms-excel", size: 20 }}
        onOpenWithOsDefaultApp={onOpenWithOsDefaultApp}
        onDownload={onDownload}
      />,
    );
    expect(screen.getByText("application/vnd.ms-excel · 20 B")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "用默认程序打开" }));
    expect(onOpenWithOsDefaultApp).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "下载" }));
    expect(onDownload).toHaveBeenCalledTimes(1);

    // 不能外部打开（web / 白名单外）时只剩下载，且它接手主按钮位。
    rerender(
      <FilePreviewBody
        name="big.bin"
        result={{ kind: "too-large" }}
        onDownload={onDownload}
      />,
    );
    expect(screen.queryByRole("button", { name: "用默认程序打开" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "下载" }));
    expect(onDownload).toHaveBeenCalledTimes(2);
  });

  it("兜底面：两个出口都不可用 → 只留说明，不渲染空按钮", () => {
    render(<FilePreviewBody name="big.bin" result={{ kind: "too-large" }} />);
    expect(screen.getByText("文件过大")).toBeTruthy();
    expect(screen.queryByText(/请下载或用系统默认/)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("压缩包二进制兜底标题是「这是压缩包」", () => {
    render(
      <FilePreviewBody
        name="pack.zip"
        result={{ kind: "binary", mime: "application/zip", size: 20 }}
      />,
    );
    expect(screen.getByText("这是压缩包")).toBeTruthy();
  });

  it("目录预览列出成员", () => {
    render(
      <FilePreviewBody
        name="pack"
        result={{
          kind: "dir",
          entries: [
            { path: "pack/a.txt", name: "a.txt", isDir: false },
            { path: "pack/docs", name: "docs", isDir: true },
          ],
        }}
      />,
    );
    expect(screen.getByText("a.txt")).toBeTruthy();
    expect(screen.getByText("docs/")).toBeTruthy();
  });
});
