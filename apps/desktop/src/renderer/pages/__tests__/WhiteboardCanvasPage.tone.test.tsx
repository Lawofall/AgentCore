// @vitest-environment jsdom
import { getBoard, saveBoardScene } from "@/services/boards";
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/boards", () => ({
  getBoard: vi.fn(),
  renameBoard: vi.fn(),
  saveBoardScene: vi.fn(),
}));
vi.mock("@/services/boardOps", () => ({
  registerBoardApplier: () => () => {},
}));
vi.mock("@/services/boardRead", () => ({
  registerBoardReader: () => () => {},
}));
vi.mock("@/lib/toast", () => ({
  notifyInfo: vi.fn(),
}));
vi.mock("@/whiteboard", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/whiteboard")>();
  const { useEffect } = await import("react");
  return {
    ...actual,
    WhiteboardCanvas: ({
      onChange,
    }: {
      onChange?: (elements: unknown[], viewport: unknown) => void;
    }) => {
      useEffect(() => {
        onChange?.([{ id: "n1", type: "rectangle" }], {
          x: 0,
          y: 0,
          zoom: 1,
        });
      }, [onChange]);
      return <div data-testid="wb" />;
    },
  };
});

import { WhiteboardCanvasPage } from "../WhiteboardCanvasPage";

const loadBoard = vi.mocked(getBoard);
const saveScene = vi.mocked(saveBoardScene);

beforeEach(() => {
  loadBoard.mockReset();
  saveScene.mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("WhiteboardCanvasPage · 冲突色", () => {
  it("已在别处更新条走 primary；重新加载不是 danger", async () => {
    loadBoard.mockResolvedValue({
      id: "b1",
      title: "测试白板",
      version: 1,
      scene: { format: "agentcore-board", schemaVersion: 1, elements: [] },
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-01T00:00:00Z",
    });
    saveScene.mockResolvedValue({
      conflict: true,
      ok: false,
      version: 2,
      board: null,
    });

    render(
      <MemoryRouter initialEntries={["/whiteboard/b1"]}>
        <Routes>
          <Route
            path="/whiteboard/:boardId"
            element={<WhiteboardCanvasPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("wb")).toBeTruthy();
    expect(screen.queryByText("即将上线")).toBeNull();
    expect(screen.queryByLabelText(/下达白板指令/)).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });

    const bar = screen.getByText(/已在别处更新/).closest("div");
    expect(bar?.className).toContain("primary");
    expect(bar?.className).not.toContain("destructive");
    expect(screen.getByText("重新加载").className).not.toContain("destructive");
  });
});
