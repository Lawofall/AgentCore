// @vitest-environment jsdom
/**
 * CEO bubble (collapseProcessSteps default true) must show wait tool rows and
 * wait-idle reasoning — omitCoordinationIdleSteps is no longer applied here.
 */
import {
  ProcessTimeline,
  shouldShowThinkingTail,
} from "@/components/chat/message-bubble/ProcessTimeline";
import type { ProcessStep } from "@/types/events";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/disclosure", () => ({
  useStreamAwareDisclosure: () => [true, vi.fn()],
  usePersistentDisclosure: () => [false, vi.fn()],
}));

afterEach(cleanup);

const emptyCards = {
  checkpoints: [] as never[],
  planReviews: [] as never[],
  teamPreviews: [] as never[],
};

function renderTimeline(process: ProcessStep[], isStreaming: boolean) {
  return render(
    <ProcessTimeline
      process={process}
      isStreaming={isStreaming}
      citations={[]}
      composingTool={null}
      fallbackContent=""
      conversationId="c1"
      {...emptyCards}
    />,
  );
}

describe("ProcessTimeline · wait visibility (CEO bubble)", () => {
  it("shows wait tool and wait-idle reasoning under default collapse", () => {
    const process: ProcessStep[] = [
      { kind: "reasoning", text: "空等听团" },
      {
        kind: "tool",
        id: "w1",
        tool_name: "wait",
        arguments: {},
        result: null,
        status: "success",
      },
      { kind: "reasoning", text: "仍在听" },
      { kind: "content", text: "对用户说一句" },
    ];
    renderTimeline(process, false);
    expect(screen.getByText("Wait")).toBeTruthy();
    expect(screen.getByText("空等听团")).toBeTruthy();
    expect(screen.getByText("仍在听")).toBeTruthy();
    expect(screen.getByText("对用户说一句")).toBeTruthy();
  });

  it("does not paint Thinking tail after a settled wait while streaming", () => {
    const process: ProcessStep[] = [
      {
        kind: "tool",
        id: "w1",
        tool_name: "wait",
        arguments: {},
        result: null,
        status: "success",
      },
    ];
    renderTimeline(process, true);
    expect(screen.getByText("Wait")).toBeTruthy();
    expect(screen.queryByText(/Thinking/i)).toBeNull();
  });

  it("paints Thinking tail after a team marker when the graph is not visible", () => {
    renderTimeline([{ kind: "team", execution_id: "e1" }], true);
    expect(screen.getByText("Thinking…")).toBeTruthy();
  });

  it("does not stack Thinking tail while a kickoff card is still pending", () => {
    render(
      <ProcessTimeline
        process={[{ kind: "team", execution_id: "e1" }]}
        isStreaming={true}
        citations={[]}
        composingTool={null}
        fallbackContent=""
        conversationId="c1"
        {...emptyCards}
        teamPreviews={[{ status: "pending" } as never]}
      />,
    );
    expect(screen.queryByText("Thinking…")).toBeNull();
  });
});

describe("shouldShowThinkingTail", () => {
  const toolDone: ProcessStep = {
    kind: "tool",
    id: "t1",
    tool_name: "web_search",
    arguments: {},
    result: null,
    status: "success",
  };
  const toolRunning: ProcessStep = { ...toolDone, status: "running" };
  const waitDone: ProcessStep = { ...toolDone, tool_name: "wait" };
  const team: ProcessStep = { kind: "team", execution_id: "e1" };
  const preview: ProcessStep = {
    kind: "team_preview",
    checkpoint_id: "tp1",
  };

  const live = {
    isStreaming: true,
    composingTool: false,
    graphVisibleAtTail: false,
    pendingUserGate: false,
  };

  it("keeps the completed-tool tail and wait S4", () => {
    expect(shouldShowThinkingTail({ ...live, last: toolDone })).toBe(true);
    expect(shouldShowThinkingTail({ ...live, last: toolRunning })).toBe(false);
    expect(shouldShowThinkingTail({ ...live, last: waitDone })).toBe(false);
  });

  it("treats marker tails as no-live-node (orchestration stand-in)", () => {
    expect(shouldShowThinkingTail({ ...live, last: team })).toBe(true);
    expect(shouldShowThinkingTail({ ...live, last: preview })).toBe(true);
  });

  it("does not stack on live reasoning/content/rework or composing", () => {
    expect(
      shouldShowThinkingTail({
        ...live,
        last: { kind: "reasoning", text: "想" },
      }),
    ).toBe(false);
    expect(
      shouldShowThinkingTail({
        ...live,
        last: { kind: "content", text: "答" },
      }),
    ).toBe(false);
    expect(shouldShowThinkingTail({ ...live, last: { kind: "rework" } })).toBe(
      false,
    );
    expect(
      shouldShowThinkingTail({ ...live, last: toolDone, composingTool: true }),
    ).toBe(false);
  });

  it("does not stack when the graph or a pending user gate is live", () => {
    expect(
      shouldShowThinkingTail({
        ...live,
        last: team,
        graphVisibleAtTail: true,
      }),
    ).toBe(false);
    expect(
      shouldShowThinkingTail({
        ...live,
        last: team,
        pendingUserGate: true,
      }),
    ).toBe(false);
    expect(
      shouldShowThinkingTail({ ...live, last: team, isStreaming: false }),
    ).toBe(false);
  });
});
