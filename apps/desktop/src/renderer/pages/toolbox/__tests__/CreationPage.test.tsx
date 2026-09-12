import { CreationPage } from "@/pages/toolbox/CreationPage";
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

const UNAVAILABLE = ["思维导图", "多维表格", "幻灯片"] as const;

function renderPage() {
  return render(
    <MemoryRouter>
      <Routes>
        <Route path="/" element={<CreationPage />} />
        <Route path="/whiteboard" element={<div>板列表</div>} />
        <Route path="/docs" element={<div>文档列表</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("CreationPage", () => {
  it("lists the suite; 白板 and 文档 are openable", () => {
    renderPage();
    expect(screen.getByRole("button", { name: "白板" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "文档" })).toBeTruthy();
    expect(screen.getByText("无限画布，自由摆元素。")).toBeTruthy();
    expect(screen.getByText("可反复编辑的长文。")).toBeTruthy();
    for (const title of UNAVAILABLE) {
      expect(screen.getByText(title)).toBeTruthy();
      expect(screen.queryByRole("button", { name: title })).toBeNull();
    }
    expect(screen.getAllByText("尚未开放")).toHaveLength(3);
    expect(screen.queryByText("可运行产物")).toBeNull();
    expect(screen.queryByText("即将开放")).toBeNull();
    expect(screen.queryByText("打开白板")).toBeNull();
  });

  it("白板 opens the board list", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "白板" }));
    expect(screen.getByText("板列表")).toBeTruthy();
  });

  it("文档 opens the doc list", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "文档" }));
    expect(screen.getByText("文档列表")).toBeTruthy();
  });
});
