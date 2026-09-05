// @vitest-environment jsdom
import { ResourceSection } from "@/components/chat/detail/sections/RunResources";
import type { AgentState, RunNode } from "@/stores/execution";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/disclosure", () => ({
  usePersistentDisclosure: (_key: string, initial: boolean) => [
    initial,
    vi.fn(),
  ],
}));

afterEach(cleanup);

function runWithUsage(
  usage: NonNullable<RunNode["usage"]>,
  extra: Partial<RunNode> = {},
): RunNode {
  return {
    usage,
    cost: extra.cost ?? null,
    model: extra.model ?? "gpt-5.6-sol",
    ...extra,
  } as RunNode;
}

const agent = { thinking: false } as AgentState;

describe("ResourceSection cache split display", () => {
  it("omitted 0/0 with input shows billing口径, not 0 命中", () => {
    render(
      <ResourceSection
        run={runWithUsage({
          input: 800,
          output: 40,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 0,
        })}
        agent={agent}
        defaultExpanded
        keyBase="t"
      />,
    );
    expect(screen.getByText(/按未命中计价 800/)).toBeTruthy();
    expect(screen.queryByText(/命中 0/)).toBeNull();
  });

  it("DeepSeek true 0 hit keeps miss=input and still bills as miss", () => {
    render(
      <ResourceSection
        run={runWithUsage({
          input: 800,
          output: 40,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 800,
        })}
        agent={agent}
        defaultExpanded
        keyBase="t"
      />,
    );
    expect(screen.getByText(/按未命中计价 800/)).toBeTruthy();
    expect(screen.queryByText(/命中 0/)).toBeNull();
  });

  it("real hits show 缓存命中 + rate, not a hit/miss partition", () => {
    render(
      <ResourceSection
        run={runWithUsage({
          input: 10_700_000,
          output: 96_000,
          reasoning: 0,
          cache_hit: 9_900_000,
          cache_miss: 182_100,
        })}
        agent={agent}
        defaultExpanded
        keyBase="t"
      />,
    );
    expect(screen.getByText(/缓存命中 9.9M（93%）/)).toBeTruthy();
    expect(screen.queryByText(/未命中/)).toBeNull();
  });
});

describe("ResourceSection ledger layout", () => {
  const billedRun = runWithUsage(
    {
      input: 10_700_000,
      output: 96_000,
      reasoning: 0,
      cache_hit: 9_900_000,
      cache_miss: 182_100,
    },
    {
      model: "deepseek-v4-flash",
      cost: {
        input: 137_000_000,
        output: 26_000_000,
        cached: 28_000_000,
        total: 163_000_000,
        currency: "USD",
      },
    },
  );

  it("defaults collapsed: header keeps the total, body stays hidden", () => {
    render(<ResourceSection run={billedRun} agent={agent} keyBase="t" />);
    expect(screen.getByText("$0.16")).toBeTruthy();
    expect(screen.queryByText("deepseek-v4-flash")).toBeNull();
    expect(screen.queryByText("输入 token")).toBeNull();
  });

  it("states cached as 其中缓存 (billed portion), not 缓存省", () => {
    render(
      <ResourceSection
        run={billedRun}
        agent={agent}
        defaultExpanded
        keyBase="t"
      />,
    );
    expect(screen.getByText(/其中缓存 \$0.03/)).toBeTruthy();
    expect(screen.queryByText(/缓存省/)).toBeNull();
    expect(screen.queryByText("成本")).toBeNull();
    expect(screen.getByText(/输入 \$0.14 · 输出 \$0.02/)).toBeTruthy();
  });

  it("keeps 思考 as a chip next to the model, not a tautological row", () => {
    render(
      <ResourceSection
        run={billedRun}
        agent={{ thinking: true } as AgentState}
        defaultExpanded
        keyBase="t"
      />,
    );
    expect(screen.getByText("deepseek-v4-flash")).toBeTruthy();
    expect(screen.getAllByText("思考")).toHaveLength(1);
  });

  it("hides 推理 0", () => {
    render(
      <ResourceSection
        run={billedRun}
        agent={agent}
        defaultExpanded
        keyBase="t"
      />,
    );
    expect(screen.queryByText(/推理 0/)).toBeNull();
  });

  it("shows 推理 only when reasoning tokens > 0", () => {
    render(
      <ResourceSection
        run={runWithUsage({
          input: 800,
          output: 40,
          reasoning: 1200,
          cache_hit: 0,
          cache_miss: 800,
        })}
        agent={agent}
        defaultExpanded
        keyBase="t"
      />,
    );
    expect(screen.getByText(/推理 1.2k/)).toBeTruthy();
  });
});
