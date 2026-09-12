// @vitest-environment jsdom

import type { PresentGitRepoStatus } from "@/lib/gitRepoStatus";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { fetchGitRepoStatus } = vi.hoisted(() => ({
  fetchGitRepoStatus: vi.fn(),
}));

vi.mock("@/lib/gitRepoStatus", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/gitRepoStatus")>();
  return { ...actual, fetchGitRepoStatus };
});

import { useGitRepoStatus } from "@/hooks/useGitRepoStatus";

function present(branch: string): PresentGitRepoStatus {
  return {
    present: true,
    branch,
    dirty: false,
    ahead: 0,
    behind: 0,
    staged: [],
    unstaged: [],
    conflicted: [],
  };
}

describe("useGitRepoStatus", () => {
  beforeEach(() => {
    fetchGitRepoStatus.mockReset();
    fetchGitRepoStatus.mockResolvedValue(null);
  });

  afterEach(() => {
    vi.clearAllMocks();
    (window as { fsApi?: unknown }).fsApi = undefined;
  });

  it("drops stale refresh when a slower request finishes after rootId switch", async () => {
    let resolveA!: (v: PresentGitRepoStatus | null) => void;
    let resolveB!: (v: PresentGitRepoStatus | null) => void;
    fetchGitRepoStatus
      .mockImplementationOnce(
        () =>
          new Promise<PresentGitRepoStatus | null>((r) => {
            resolveA = r;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise<PresentGitRepoStatus | null>((r) => {
            resolveB = r;
          }),
      );

    const { result, rerender } = renderHook(
      ({ rootId, enabled }) => useGitRepoStatus(rootId, enabled),
      { initialProps: { rootId: "root-a", enabled: true } },
    );

    await waitFor(() =>
      expect(fetchGitRepoStatus).toHaveBeenCalledWith("root-a"),
    );

    rerender({ rootId: "root-b", enabled: true });
    await waitFor(() =>
      expect(fetchGitRepoStatus).toHaveBeenCalledWith("root-b"),
    );

    await act(async () => {
      resolveA(present("branch-a"));
    });
    expect(result.current.status).toBeNull();

    await act(async () => {
      resolveB(present("branch-b"));
    });
    await waitFor(() => expect(result.current.status?.branch).toBe("branch-b"));
  });

  it("does not subscribe to workspace file events; still polls on focus", async () => {
    const watch = vi.fn().mockResolvedValue(undefined);
    const unwatch = vi.fn().mockResolvedValue(undefined);
    const onChanged = vi.fn(() => () => {});
    window.fsApi = {
      watch,
      unwatch,
      onChanged,
    } as unknown as typeof window.fsApi;

    fetchGitRepoStatus.mockResolvedValue(present("main"));

    const { unmount } = renderHook(() => useGitRepoStatus("root-1", true));

    await waitFor(() =>
      expect(fetchGitRepoStatus).toHaveBeenCalledWith("root-1"),
    );
    expect(watch).not.toHaveBeenCalled();
    expect(onChanged).not.toHaveBeenCalled();

    fetchGitRepoStatus.mockClear();
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() =>
      expect(fetchGitRepoStatus).toHaveBeenCalledWith("root-1"),
    );

    unmount();
    expect(unwatch).not.toHaveBeenCalled();
  });

  it("no-ops safely when fsApi is missing or lacks watch", async () => {
    fetchGitRepoStatus.mockResolvedValue(null);

    const { rerender, unmount } = renderHook(
      ({ rootId, enabled }) => useGitRepoStatus(rootId, enabled),
      { initialProps: { rootId: "r1", enabled: true } },
    );
    await waitFor(() => expect(fetchGitRepoStatus).toHaveBeenCalled());

    window.fsApi = {} as unknown as typeof window.fsApi;
    rerender({ rootId: "r2", enabled: true });
    await waitFor(() => expect(fetchGitRepoStatus).toHaveBeenCalledWith("r2"));

    unmount();
  });

  it("clears status when disabled or rootId is absent", async () => {
    fetchGitRepoStatus.mockResolvedValue(present("main"));
    const { result, rerender } = renderHook(
      ({ rootId, enabled }) => useGitRepoStatus(rootId, enabled),
      { initialProps: { rootId: "r1" as string | null, enabled: true } },
    );
    await waitFor(() => expect(result.current.status?.branch).toBe("main"));

    rerender({ rootId: "r1", enabled: false });
    await waitFor(() => expect(result.current.status).toBeNull());

    rerender({ rootId: "r1", enabled: true });
    await waitFor(() => expect(result.current.status?.branch).toBe("main"));

    rerender({ rootId: null, enabled: true });
    await waitFor(() => expect(result.current.status).toBeNull());
  });
});
