// @vitest-environment jsdom
/**
 * conversationId 切换时须先清空旧 binding，再异步 refresh——避免 Git chip 短暂
 * 带着上一会话的 effective.rootId。
 */
import { useWorkspaceModeState } from "@/components/workspace/WorkspaceModeControl";
import { getConversations } from "@/hooks/useConversations";
import { getFolders } from "@/hooks/useFolders";
import { getWorkspaceBinding } from "@/services/workspaceBinding";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({
  getConversations: vi.fn(() => []),
}));
vi.mock("@/hooks/useFolders", () => ({
  getFolders: vi.fn(() => []),
}));
vi.mock("@/services/workspaceBinding", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/workspaceBinding")>();
  return {
    ...actual,
    getWorkspaceBinding: vi.fn(),
  };
});

const getBinding = vi.mocked(getWorkspaceBinding);
const getConvs = vi.mocked(getConversations);
const getFldrs = vi.mocked(getFolders);

const bindingA = {
  mode: "local" as const,
  scope: "conversation" as const,
  rootId: "root-a",
  source: "explicit" as const,
};
const bindingB = {
  mode: "local" as const,
  scope: "conversation" as const,
  rootId: "root-b",
  source: "explicit" as const,
};

beforeEach(() => {
  getBinding.mockReset();
  getConvs.mockReset();
  getFldrs.mockReset();
  getConvs.mockReturnValue([]);
  getFldrs.mockReturnValue([]);
  (window as unknown as { fsApi?: unknown }).fsApi = {
    listRoots: vi.fn().mockResolvedValue([]),
  };
});

describe("useWorkspaceModeState conversation switch", () => {
  it("clears binding before the next refresh resolves (no stale effective.rootId)", async () => {
    let resolveB!: (v: typeof bindingB) => void;
    getBinding.mockImplementation((id: string) => {
      if (id === "conv-a") return Promise.resolve(bindingA);
      return new Promise((resolve) => {
        resolveB = resolve;
      });
    });

    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useWorkspaceModeState(id),
      { initialProps: { id: "conv-a" as string | null } },
    );

    await waitFor(() => {
      expect(result.current?.effective.rootId).toBe("root-a");
    });

    await act(async () => {
      rerender({ id: "conv-b" });
    });

    // Sync clear window: hook returns null until new binding lands.
    expect(result.current).toBeNull();

    await act(async () => {
      resolveB(bindingB);
    });

    await waitFor(() => {
      expect(result.current?.effective.rootId).toBe("root-b");
    });
  });

  it("still loads binding on first mount", async () => {
    getBinding.mockResolvedValue(bindingA);

    const { result } = renderHook(() => useWorkspaceModeState("conv-a"));

    expect(result.current).toBeNull();

    await waitFor(() => {
      expect(result.current?.binding.rootId).toBe("root-a");
      expect(result.current?.effective.rootId).toBe("root-a");
    });
  });

  it("drops stale binding when a slower prior refresh finishes after switch", async () => {
    let resolveA!: (v: typeof bindingA) => void;
    let resolveB!: (v: typeof bindingB) => void;
    getBinding.mockImplementation((id: string) => {
      if (id === "conv-a") {
        return new Promise((resolve) => {
          resolveA = resolve;
        });
      }
      return new Promise((resolve) => {
        resolveB = resolve;
      });
    });

    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useWorkspaceModeState(id),
      { initialProps: { id: "conv-a" as string | null } },
    );

    // The control always reads through to the server: a cached binding could be
    // up to a TTL stale, and the mode chip must not show the wrong workspace.
    const fresh = { fresh: true };
    await waitFor(() =>
      expect(getBinding).toHaveBeenCalledWith("conv-a", fresh),
    );

    await act(async () => {
      rerender({ id: "conv-b" });
    });
    expect(result.current).toBeNull();
    await waitFor(() =>
      expect(getBinding).toHaveBeenCalledWith("conv-b", fresh),
    );

    await act(async () => {
      resolveA(bindingA);
    });
    // Stale A must not reappear while B is still pending.
    expect(result.current).toBeNull();

    await act(async () => {
      resolveB(bindingB);
    });
    await waitFor(() => {
      expect(result.current?.effective.rootId).toBe("root-b");
    });
  });

  it("does not write binding after unmount when the in-flight lookup rejects", async () => {
    let rejectBinding!: (reason: unknown) => void;
    getBinding.mockImplementation(
      () =>
        new Promise((_, reject) => {
          rejectBinding = reject;
        }),
    );

    const { unmount } = renderHook(() => useWorkspaceModeState("conv-a"));
    await waitFor(() =>
      expect(getBinding).toHaveBeenCalledWith("conv-a", { fresh: true }),
    );
    unmount();

    await act(async () => {
      rejectBinding(new ReferenceError("window is not defined"));
    });
  });

  it("does not write binding after unmount when the in-flight lookup resolves", async () => {
    let resolveBinding!: (value: typeof bindingA) => void;
    getBinding.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveBinding = resolve;
        }),
    );

    const { unmount } = renderHook(() => useWorkspaceModeState("conv-a"));
    await waitFor(() =>
      expect(getBinding).toHaveBeenCalledWith("conv-a", { fresh: true }),
    );
    unmount();

    await act(async () => {
      resolveBinding(bindingA);
    });
  });
});
