// @vitest-environment jsdom
/**
 * finish_guard rework chip: streaming empty-body → in-progress copy;
 * after content / settled → past-tense done copy.
 */
import { ProcessTimeline } from "@/components/chat/message-bubble/ProcessTimeline";
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

describe("ProcessTimeline rework chip", () => {
  it("shows in-progress copy while streaming with no content after rework", () => {
    renderTimeline(
      [{ kind: "reasoning", text: "核验" }, { kind: "rework" }],
      true,
    );
    expect(screen.getByText("正在按规则修订…")).toBeTruthy();
    expect(screen.queryByText("引用/格式核验后已重写")).toBeNull();
  });

  it("shows done copy after rewrite content arrives while still streaming", () => {
    renderTimeline(
      [{ kind: "rework" }, { kind: "content", text: "重写正文" }],
      true,
    );
    expect(screen.getByText("引用/格式核验后已重写")).toBeTruthy();
    expect(screen.queryByText("正在按规则修订…")).toBeNull();
  });

  it("shows done copy when settled with empty body after rework", () => {
    renderTimeline([{ kind: "rework" }], false);
    expect(screen.getByText("引用/格式核验后已重写")).toBeTruthy();
    expect(screen.queryByText("正在按规则修订…")).toBeNull();
  });
});
