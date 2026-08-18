// @vitest-environment jsdom
/**
 * 同组件切 whiteboard/:boardId 时，在途的旧 getBoard 不得把 A 板画到 B；
 * persistScene 也不得在来源 id 与当前路由不一致时写回。
 */
import { getBoard, saveBoardScene } from "@/services/boards";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
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

function board(id: string, title: string) {
  return {
    id,
    title,
    version: 1,
    scene: { format: "agentcore-board" as const, schemaVersion: 1, elements: [] },
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function Harness() {
  const navigate = useNavigate();
  return (
    <>
      <button
        type="button"
        data-testid="to-b"
        onClick={() => navigate("/whiteboard/board-b")}
      >
        to-b
      </button>
      <WhiteboardCanvasPage />
    </>
  );
}

function renderAt(startId: string) {
  return render(
    <MemoryRouter initialEntries={[`/whiteboard/${startId}`]}>
      <Routes>
        <Route path="/whiteboard/:boardId" element={<Harness />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  loadBoard.mockReset();
  saveScene.mockReset();
  saveScene.mockResolvedValue({
    conflict: false,
    ok: true,
    version: 2,
    board: null,
  });
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("WhiteboardCanvasPage · 切 id 旧请求晚回", () => {
  it("慢请求晚回不得把 A 板标题/画布落到 B 的 URL 上", async () => {
    const pendingA = deferred<ReturnType<typeof board>>();
    const pendingB = deferred<ReturnType<typeof board>>();
    loadBoard.mockImplementation((id: string) => {
      if (id === "board-a") return pendingA.promise;
      if (id === "board-b") return pendingB.promise;
      return Promise.reject(new Error(`unexpected board ${id}`));
    });

    renderAt("board-a");
    await flushMicrotasks();
    expect(loadBoard).toHaveBeenCalledWith("board-a");

    fireEvent.click(screen.getByTestId("to-b"));
    await flushMicrotasks();
    expect(loadBoard).toHaveBeenCalledWith("board-b");

    await act(async () => {
      pendingA.resolve(board("board-a", "板A"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.queryByDisplayValue("板A")).toBeNull();
    expect(screen.queryByTestId("wb")).toBeNull();

    await act(async () => {
      pendingB.resolve(board("board-b", "板B"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByDisplayValue("板B")).toBeTruthy();
    expect(screen.getByTestId("wb")).toBeTruthy();
    expect(screen.queryByDisplayValue("板A")).toBeNull();
  });

  it("A 已落地后切到 B：在途 debounce 不得把 A 的画布存进 B", async () => {
    const pendingB = deferred<ReturnType<typeof board>>();
    loadBoard.mockImplementation((id: string) => {
      if (id === "board-a") return Promise.resolve(board("board-a", "板A"));
      if (id === "board-b") return pendingB.promise;
      return Promise.reject(new Error(`unexpected board ${id}`));
    });

    renderAt("board-a");
    await flushMicrotasks();
    expect(screen.getByDisplayValue("板A")).toBeTruthy();
    expect(screen.getByTestId("wb")).toBeTruthy();

    fireEvent.click(screen.getByTestId("to-b"));
    await flushMicrotasks();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(saveScene).not.toHaveBeenCalled();

    await act(async () => {
      pendingB.resolve(board("board-b", "板B"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByDisplayValue("板B")).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(saveScene).toHaveBeenCalled();
    for (const call of saveScene.mock.calls) {
      expect(call[0]).toBe("board-b");
    }
  });

  it("B 已落地后再收到 A 的慢响应：不改标题，自动保存仍只写 B", async () => {
    const pendingA = deferred<ReturnType<typeof board>>();
    const pendingB = deferred<ReturnType<typeof board>>();
    loadBoard.mockImplementation((id: string) => {
      if (id === "board-a") return pendingA.promise;
      if (id === "board-b") return pendingB.promise;
      return Promise.reject(new Error(`unexpected board ${id}`));
    });

    renderAt("board-a");
    await flushMicrotasks();
    fireEvent.click(screen.getByTestId("to-b"));
    await flushMicrotasks();

    await act(async () => {
      pendingB.resolve(board("board-b", "板B"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByDisplayValue("板B")).toBeTruthy();

    await act(async () => {
      pendingA.resolve(board("board-a", "板A"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByDisplayValue("板B")).toBeTruthy();
    expect(screen.queryByDisplayValue("板A")).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(saveScene).toHaveBeenCalled();
    for (const call of saveScene.mock.calls) {
      expect(call[0]).toBe("board-b");
    }
  });
});
