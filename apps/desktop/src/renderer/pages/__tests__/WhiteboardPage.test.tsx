// @vitest-environment jsdom
import { createBoard, listBoards } from "@/services/boards";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/boards", () => ({
  listBoards: vi.fn(),
  createBoard: vi.fn(),
  deleteBoard: vi.fn(),
}));

import { WhiteboardPage } from "../WhiteboardPage";

const list = vi.mocked(listBoards);
const create = vi.mocked(createBoard);

afterEach(() => {
  cleanup();
});

describe("WhiteboardPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    list.mockResolvedValue([]);
    create.mockResolvedValue({
      id: "b1",
      title: "未命名白板",
      version: 1,
      conversation_id: null,
      created_at: "2026-09-09T00:00:00Z",
      updated_at: "2026-09-09T00:00:00Z",
    });
  });

  it("新建不出现归入文件夹，创建不带 folderId", async () => {
    render(
      <MemoryRouter>
        <WhiteboardPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("还没有白板")).toBeTruthy();
    expect(screen.queryByText("归入文件夹（可选）")).toBeNull();
    expect(screen.queryByText("未归入文件夹")).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "新建白板" })[0]);
    });
    expect(create).toHaveBeenCalledWith();
  });
});
