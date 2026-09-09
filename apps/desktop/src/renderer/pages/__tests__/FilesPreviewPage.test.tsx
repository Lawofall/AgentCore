// @vitest-environment jsdom
import { FilesPreviewPage } from "@/pages/FilesPreviewPage";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/files/FileTree", () => ({
  FileTree: () => <div data-testid="file-tree" />,
}));

vi.mock("@/components/files/fileWorkbench/EntriesSection", () => ({
  EntriesSection: () => null,
}));

vi.mock("@/components/files/fileWorkbench/createScopeEntry", () => ({
  createAndOpenScopeEntry: vi.fn(),
}));

afterEach(cleanup);

function renderPreview() {
  return render(
    <MemoryRouter initialEntries={["/preview/files"]}>
      <Routes>
        <Route path="/preview/files" element={<FilesPreviewPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("FilesPreviewPage", () => {
  it("does not pin account prompts or 最近更新; only the folder tree", () => {
    renderPreview();
    expect(screen.queryByText("全局设定")).toBeNull();
    expect(screen.queryByText("最近更新")).toBeNull();
    expect(
      screen.queryByRole("link", {
        name: "所有对话共用的提示词在工具箱",
      }),
    ).toBeNull();
    expect(screen.getByTestId("file-tree")).toBeTruthy();
  });
});
