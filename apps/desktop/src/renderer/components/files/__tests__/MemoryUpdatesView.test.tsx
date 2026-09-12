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

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onOpenLeaf = vi.fn();
  return {
    onOpenLeaf,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <MemoryUpdatesView onOpenLeaf={onOpenLeaf} />
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
  it("有最近学到页头", async () => {
    renderView();
    await waitFor(() => {
      expect(screen.getByText("还没有学到的内容")).toBeTruthy();
    });
    expect(screen.getByText("最近学到")).toBeTruthy();
    expect(screen.getByTestId("memory-updates-view")).toBeTruthy();
  });
});
