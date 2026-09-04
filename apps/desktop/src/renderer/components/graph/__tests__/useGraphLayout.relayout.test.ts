// @vitest-environment jsdom
/**
 * 结构重算不得把 layoutReady 打回 false（否则 GraphView 卸载 ReactFlow → 整图闪烁）。
 * 白板模型：测高不得触发二次 ELK；仅结构变更重排。
 */
import { NODE_HEIGHT } from "@/lib/elk-layout";
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

import { clearLayoutResultCache } from "../layoutResultCache";
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

describe("useGraphLayout · keep graph during relayout", () => {
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

  it("keeps layoutReady true across structural append (追加委派)", async () => {
    const emptyExpand = new Set<string>();
    const { result, rerender } = renderHook(
      ({ execution }: { execution: Execution }) =>
        useGraphLayout(execution, "tree", "view", emptyExpand),
      { initialProps: { execution: exec(["w1"]) } },
    );

    await waitFor(() => expect(result.current.layoutReady).toBe(true));
    const readySnapshots: boolean[] = [];

    await act(async () => {
      rerender({ execution: exec(["w1", "w2"]) });
      // 同步读：结构 effect 已跑但 ELK 未完成时不得 blank。
      readySnapshots.push(result.current.layoutReady);
    });

    expect(readySnapshots.every((v) => v)).toBe(true);
    await waitFor(() => {
      expect(result.current.layoutReady).toBe(true);
      expect(Object.keys(result.current.positions)).toEqual(
        expect.arrayContaining(["w1", "w2"]),
      );
    });
  });
});

describe("useGraphLayout · structure identity does not secondary-ELK", () => {
  beforeEach(() => {
    clearLayoutResultCache();
    computeLayout.mockReset();
    let n = 0;
    computeLayout.mockImplementation(
      async (
        nodeIds: string[],
        _edges: unknown,
        _layout: unknown,
        _bookends: unknown,
        _subTeams: unknown,
        _spacing: unknown,
        nodeSizes: Record<string, { width: number; height: number }>,
      ) => {
        n += 1;
        const positions: Record<string, { x: number; y: number }> = {};
        let y = 0;
        for (const id of nodeIds) {
          positions[id] = { x: n * 10, y };
          y += (nodeSizes?.[id]?.height ?? NODE_HEIGHT) + 56;
        }
        return {
          positions,
          width: 400 + n,
          height: y + 24,
          groups: [],
        };
      },
    );
  });

  it("same topology, new execution object: no second ELK; footprint stays NODE_HEIGHT", async () => {
    const emptyExpand = new Set<string>();
    const first = exec(["be", "fe"]);
    const { result, rerender } = renderHook(
      ({ execution }: { execution: Execution }) =>
        useGraphLayout(execution, "leftright", "view", emptyExpand),
      { initialProps: { execution: first } },
    );

    await waitFor(() => expect(result.current.layoutReady).toBe(true));
    const afterStructural = computeLayout.mock.calls.length;
    expect(afterStructural).toBeGreaterThanOrEqual(1);

    const structuralSizes = computeLayout.mock.calls.at(-1)?.[6] as Record<
      string,
      { width: number; height: number }
    >;
    expect(structuralSizes.be.height).toBe(NODE_HEIGHT);
    expect(structuralSizes.fe.height).toBe(NODE_HEIGHT);

    rerender({ execution: { ...first, runs: [...first.runs] } });

    expect(computeLayout.mock.calls.length).toBe(afterStructural);
    expect(result.current.nodeSizes.be.height).toBe(NODE_HEIGHT);
    expect(result.current.nodeSizes.fe.height).toBe(NODE_HEIGHT);
  });
});
