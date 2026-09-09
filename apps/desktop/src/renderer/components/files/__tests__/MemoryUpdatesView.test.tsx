import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryUpdatesView } from "../MemoryUpdatesView";

vi.mock("@/services/memory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/memory")>();
  return {
    ...actual,
    listMemoryUpdates: vi.fn(async () => []),
    listDisputedMemoryLines: vi.fn(async () => ({
      lines: [],
      maxPerEntry: 50,
    })),
  };
});

vi.mock("@/hooks/useFolders", () => ({
  getFolders: () => [],
}));

const { listMemoryUpdates } = await import("@/services/memory");

function renderView(embedded = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onOpenLeaf = vi.fn();
  return {
    onOpenLeaf,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <MemoryUpdatesView embedded={embedded} onOpenLeaf={onOpenLeaf} />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

beforeEach(() => {
  vi.mocked(listMemoryUpdates).mockReset();
  vi.mocked(listMemoryUpdates).mockResolvedValue([]);
});

afterEach(cleanup);

describe("MemoryUpdatesView", () => {
  it("standalone 有记忆动态页头", async () => {
    renderView(false);
    await waitFor(() => {
      expect(screen.getByText("还没有记忆更新")).toBeTruthy();
    });
    expect(screen.getByText("记忆动态")).toBeTruthy();
    expect(screen.getByTestId("memory-updates-view")).toBeTruthy();
  });

  it("embedded 不再套记忆动态大页头", async () => {
    renderView(true);
    await waitFor(() => {
      expect(screen.getByText("还没有记忆更新")).toBeTruthy();
    });
    expect(screen.queryByText("记忆动态")).toBeNull();
    expect(screen.queryByText("AI 最近从各处对话里记下的内容")).toBeNull();
  });
});
