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

function runWithUsage(usage: NonNullable<RunNode["usage"]>): RunNode {
  return {
    usage,
    cost: null,
    model: "gpt-5.6-sol",
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
});
