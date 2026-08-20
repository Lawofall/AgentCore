// @vitest-environment jsdom
/**
 * Layout result LRU: same structure + fitMode + layoutKind must not re-run ELK
 * across remount (session switch → back). fitMode / layoutKind miss separately.
 */
import type { Execution } from "@/stores/execution";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const computeLayout = vi.fn();

vi.mock("@/lib/elk-layout", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/elk-layout")>();
  return {
    ...actual,
    computeLayout: (...args: unknown[]) => computeLayout(...args),
  };
});

import {
  GRAPH_LAYOUT_CACHE_LIMIT,
  clearLayoutResultCache,
  getCachedLayout,
  layoutCacheKey,
  layoutResultCacheSize,
  setCachedLayout,
} from "../layoutResultCache";
import { useGraphLayout } from "../useGraphLayout";

function exec(runIds: string[]): Execution {
  return {
    runs: [
      {
        id: "captain",
        kind: "captain",
        dependsOn: [],
        agentId: "ceo",
        task: "",
        status: "running",
        parentRunId: null,
        replacesRunId: null,
      },
      ...runIds.map((id) => ({
        id,
        kind: "agent" as const,
        dependsOn: [] as string[],
        agentId: id,
        task: id,
        status: "running" as const,
        parentRunId: null,
        replacesRunId: null,
      })),
    ],
  } as unknown as Execution;
}

describe("layoutResultCache LRU", () => {
  beforeEach(() => {
    clearLayoutResultCache();
  });

  it("evicts oldest when over GRAPH_LAYOUT_CACHE_LIMIT", () => {
    const empty = {
      positions: {},
      edges: [],
      width: 1,
      height: 1,
      nodeSizes: {},
      groups: [],
      actCards: [],
    };
    for (let i = 0; i < GRAPH_LAYOUT_CACHE_LIMIT + 3; i++) {
      setCachedLayout(`k${i}`, { ...empty, width: i });
    }
    expect(layoutResultCacheSize()).toBe(GRAPH_LAYOUT_CACHE_LIMIT);
    expect(getCachedLayout("k0")).toBeUndefined();
    expect(getCachedLayout("k1")).toBeUndefined();
    expect(getCachedLayout("k2")).toBeUndefined();
    expect(getCachedLayout(`k${GRAPH_LAYOUT_CACHE_LIMIT + 2}`)?.width).toBe(
      GRAPH_LAYOUT_CACHE_LIMIT + 2,
    );
  });

  it("layoutCacheKey distinguishes fitMode and layoutKind", () => {
    const a = layoutCacheKey("struct", "tree", "view");
    const b = layoutCacheKey("struct", "tree", "width");
    const c = layoutCacheKey("struct", "leftright", "view");
    expect(a).not.toBe(b);
    expect(a).not.toBe(c);
  });
});

describe("useGraphLayout · layout result cache", () => {
  beforeEach(() => {
    clearLayoutResultCache();
    computeLayout.mockReset();
    let n = 0;
    computeLayout.mockImplementation(async (nodeIds: string[]) => {
      n += 1;
      const positions: Record<string, { x: number; y: number }> = {};
      for (const id of nodeIds) {
        positions[id] = { x: n * 10, y: n * 20 };
      }
      return {
        positions,
        width: 400 + n,
        height: 300 + n,
        groups: [],
      };
    });
  });

  it("skips ELK on remount with same structure (session switch back)", async () => {
    const emptyExpand = new Set<string>();
    const execution = exec(["w1"]);

    const first = renderHook(() =>
      useGraphLayout(execution, "tree", "view", emptyExpand),
    );
    await waitFor(() => expect(first.result.current.layoutReady).toBe(true));
    const afterFirst = computeLayout.mock.calls.length;
    expect(afterFirst).toBeGreaterThanOrEqual(1);
    const positions = { ...first.result.current.positions };
    first.unmount();

    const second = renderHook(() =>
      useGraphLayout(execution, "tree", "view", emptyExpand),
    );
    await waitFor(() => expect(second.result.current.layoutReady).toBe(true));
    expect(computeLayout.mock.calls.length).toBe(afterFirst);
    expect(second.result.current.positions).toEqual(positions);
    second.unmount();
  });

  it("still applies layoutReady on cache hit", async () => {
    const emptyExpand = new Set<string>();
    const execution = exec(["w1"]);

    const first = renderHook(() =>
      useGraphLayout(execution, "leftright", "view", emptyExpand),
    );
    await waitFor(() => expect(first.result.current.layoutReady).toBe(true));
    first.unmount();

    const second = renderHook(() =>
      useGraphLayout(execution, "leftright", "view", emptyExpand),
    );
    // Sync cache hit in effect → ready without waiting on ELK.
    await waitFor(() => expect(second.result.current.layoutReady).toBe(true));
    expect(second.result.current.bbox).not.toBeNull();
    expect(Object.keys(second.result.current.positions).length).toBeGreaterThan(
      0,
    );
    second.unmount();
  });

  it("misses cache when fitMode changes", async () => {
    const emptyExpand = new Set<string>();
    const execution = exec(["w1"]);

    const { result, rerender } = renderHook(
      ({ fit }: { fit: "view" | "width" }) =>
        useGraphLayout(execution, "tree", fit, emptyExpand),
      { initialProps: { fit: "view" as "view" | "width" } },
    );
    await waitFor(() => expect(result.current.layoutReady).toBe(true));
    const afterView = computeLayout.mock.calls.length;
    expect(afterView).toBeGreaterThanOrEqual(1);

    await act(async () => {
      rerender({ fit: "width" });
    });
    await waitFor(() =>
      expect(computeLayout.mock.calls.length).toBeGreaterThan(afterView),
    );
  });

  it("misses cache when layoutKind changes", async () => {
    const emptyExpand = new Set<string>();
    const execution = exec(["w1"]);

    const { result, rerender } = renderHook(
      ({ kind }: { kind: "tree" | "leftright" }) =>
        useGraphLayout(execution, kind, "view", emptyExpand),
      { initialProps: { kind: "tree" as "tree" | "leftright" } },
    );
    await waitFor(() => expect(result.current.layoutReady).toBe(true));
    const afterTree = computeLayout.mock.calls.length;
    expect(afterTree).toBeGreaterThanOrEqual(1);

    await act(async () => {
      rerender({ kind: "leftright" });
    });
    await waitFor(() =>
      expect(computeLayout.mock.calls.length).toBeGreaterThan(afterTree),
    );
  });
});
