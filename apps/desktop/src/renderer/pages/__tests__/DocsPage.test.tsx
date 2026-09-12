// @vitest-environment jsdom
import { listDocs } from "@/services/docs";
import { listFolders, listFoldersSharedWithMe } from "@/services/folders";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/docs", () => ({
  listDocs: vi.fn(),
  createDoc: vi.fn(),
  deleteDoc: vi.fn(),
}));

vi.mock("@/services/folders", () => ({
  listFolders: vi.fn(),
  listFoldersSharedWithMe: vi.fn(),
}));

import { DocsPage } from "../DocsPage";

const list = vi.mocked(listDocs);
const folders = vi.mocked(listFolders);
const shared = vi.mocked(listFoldersSharedWithMe);

afterEach(() => {
  cleanup();
});

describe("DocsPage", () => {
  beforeEach(() => {
    list.mockReset();
    folders.mockReset();
    shared.mockReset();
    list.mockResolvedValue([]);
    folders.mockResolvedValue([]);
    shared.mockResolvedValue([]);
  });

  it("empty state offers 新建文档 and opens the folder picker", async () => {
    render(
      <MemoryRouter>
        <DocsPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("还没有文档")).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "新建文档" })[0]);
    });
    expect(await screen.findByText(/还没有可写入的云文件夹/)).toBeTruthy();
  });
});
