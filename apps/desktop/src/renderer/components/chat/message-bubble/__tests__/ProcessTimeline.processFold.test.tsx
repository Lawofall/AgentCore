// @vitest-environment jsdom
/**
 * 完成态过程折：工具/思考收进摘要；用户可见正文（含中间段）留在折外。
 * disclosure mock 跟真实收场默认（直播展开、收场收起）。
 */
import { ProcessTimeline } from "@/components/chat/message-bubble/ProcessTimeline";
import type { CheckpointDisplay } from "@/stores/conversation";
import type { ProcessStep } from "@/types/events";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/disclosure", () => ({
  useStreamAwareDisclosure: (
    _key: string | null,
    live: boolean,
    opts?: { liveDefault?: boolean; settledDefault?: boolean },
  ) => {
    const liveDefault = opts?.liveDefault ?? true;
    const settledDefault = opts?.settledDefault ?? false;
    return [live ? liveDefault : settledDefault, vi.fn()];
  },
  usePersistentDisclosure: () => [false, vi.fn()],
}));

afterEach(cleanup);

const emptyCards = {
  checkpoints: [] as never[],
};

const toolDone: ProcessStep = {
  kind: "tool",
  id: "t1",
  tool_name: "web_search",
  arguments: {},
  result: null,
  status: "success",
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

describe("ProcessTimeline · 正文不进过程折", () => {
  const process: ProcessStep[] = [
    { kind: "content", text: "我先找日志目录" },
    toolDone,
    { kind: "content", text: "清晰度是 1080p" },
  ];

  it("keeps mid-content and the trailing answer visible; tools stay behind the summary", () => {
    renderTimeline(process, false);
    expect(screen.getByText("Used 1 tool")).toBeTruthy();
    expect(screen.getByText("我先找日志目录")).toBeTruthy();
    expect(screen.queryByText("Search web")).toBeNull();
    expect(screen.getByText("清晰度是 1080p")).toBeTruthy();
  });

  it("keeps mid-content visible while streaming", () => {
    renderTimeline(process, true);
    expect(screen.queryByText("Used 1 tool")).toBeNull();
    expect(screen.getByText("我先找日志目录")).toBeTruthy();
    expect(screen.getByText("清晰度是 1080p")).toBeTruthy();
  });

  it("keeps fallback deliverable visible when there is no content step", () => {
    render(
      <ProcessTimeline
        process={[toolDone]}
        isStreaming={false}
        citations={[]}
        composingTool={null}
        fallbackContent="清晰度是 1080p"
        conversationId="c1"
        {...emptyCards}
      />,
    );
    expect(screen.getByText("Used 1 tool")).toBeTruthy();
    expect(screen.getByText("清晰度是 1080p")).toBeTruthy();
  });

  it("hides a resolved ask behind the summary and keeps lead-in plus trailing answer", () => {
    const resolvedAsk: CheckpointDisplay = {
      id: "cp-1",
      question: "你心里的「Agent 生态」更接近哪种？",
      questions: [],
      intent: "decision",
      status: "resolved",
      decision: "continue",
      note: "· 你心里的「Agent 生态」更接近哪种？：都不太对",
      selected: [],
    };
    render(
      <ProcessTimeline
        process={[
          { kind: "content", text: "先对齐方向" },
          { kind: "checkpoint", checkpoint_id: "cp-1" },
          toolDone,
          { kind: "content", text: "按这个方向继续" },
        ]}
        isStreaming={false}
        citations={[]}
        composingTool={null}
        fallbackContent=""
        conversationId="c1"
        checkpoints={[resolvedAsk]}
      />,
    );
    expect(screen.getByText("Used 1 tool")).toBeTruthy();
    expect(screen.getByText("先对齐方向")).toBeTruthy();
    expect(screen.queryByText(/都不太对/)).toBeNull();
    expect(screen.getByText("按这个方向继续")).toBeTruthy();
  });

  it("keeps the lead-in before a pending ask visible", () => {
    const pendingAsk: CheckpointDisplay = {
      id: "cp-pending",
      question: "选哪条？",
      questions: [],
      intent: "decision",
      status: "pending",
      decision: null,
      note: "",
      selected: [],
    };
    render(
      <ProcessTimeline
        process={[
          { kind: "content", text: "你选哪个？" },
          { kind: "checkpoint", checkpoint_id: "cp-pending" },
          toolDone,
        ]}
        isStreaming={false}
        citations={[]}
        composingTool={null}
        fallbackContent=""
        conversationId="c1"
        checkpoints={[pendingAsk]}
      />,
    );
    expect(screen.getByText("Used 1 tool")).toBeTruthy();
    expect(screen.getByText("你选哪个？")).toBeTruthy();
  });

  it("places the collapsed summary as a caption immediately before the answer", () => {
    const { container } = renderTimeline(
      [
        { kind: "reasoning", text: "先想" },
        toolDone,
        { kind: "content", text: "## 结论\n\n不是。" },
      ],
      false,
    );
    const turn = container.querySelector(".process-turn");
    const summary = container.querySelector(".process-summary");
    const answer = container.querySelector(".process-answer");
    expect(turn).toBeTruthy();
    expect(turn?.className).not.toContain("space-y-2");
    expect(summary).toBeTruthy();
    expect(answer).toBeTruthy();
    expect(summary?.nextElementSibling).toBe(answer);
    expect(screen.getByText("Thought 1 step · Used 1 tool")).toBeTruthy();
    expect(screen.getByText("结论")).toBeTruthy();
  });

  it("does not leave a graph slot in the reply", () => {
    const { container } = renderTimeline(
      [
        { kind: "reasoning", text: "先想" },
        toolDone,
        { kind: "content", text: "结论" },
        { kind: "team", execution_id: "e1" },
      ],
      false,
    );
    expect(container.querySelector(".process-answer")).toBeTruthy();
    expect(container.querySelector(".process-slot")).toBeNull();
  });
});
