import { StageCard } from "@/components/chat/StageCard";
import type { InteractionEntry } from "@/stores/interactions";
// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

function entry(
  status: InteractionEntry["status"] = "pending",
  resolution?: Record<string, unknown>,
): InteractionEntry {
  return {
    kind: "stage_card",
    id: "sc_1",
    conversationId: "c1",
    messageId: "m1",
    status,
    payload: {
      motion: "是否应开辩",
      sides: [
        { key: "pro", name: "正方", stance: "应开" },
        { key: "con", name: "反方", stance: "暂缓" },
      ],
      form: "debate",
      rationale: "真对立轴",
      thorough: true,
      max_rounds: 5,
    },
    resolution,
  };
}

describe("StageCard", () => {
  it("pending is not a debate entry", () => {
    render(<StageCard entry={entry("pending")} />);
    expect(screen.getByTestId("stage-card")).toBeTruthy();
    expect(screen.getByText("此开辩入口已下线")).toBeTruthy();
    expect(screen.queryByText("按此开辩")).toBeNull();
    expect(screen.queryByText("先补充调研")).toBeNull();
    expect(screen.queryByText("调整命题")).toBeNull();
  });

  it("shows orphaned copy without action buttons", () => {
    render(<StageCard entry={entry("orphaned")} />);
    expect(screen.getByText(/已失效/)).toBeTruthy();
    expect(screen.queryByText("按此开辩")).toBeNull();
  });

  it("shows resolved copy without action buttons", () => {
    render(
      <StageCard entry={entry("resolved", { decision: "start_debate" })} />,
    );
    expect(screen.getByText("已按此开辩")).toBeTruthy();
    expect(screen.queryByText("按此开辩")).toBeNull();
  });

  it("shows research_first resolved copy", () => {
    render(
      <StageCard entry={entry("resolved", { decision: "research_first" })} />,
    );
    expect(screen.getByText("已选择先补充调研")).toBeTruthy();
    expect(screen.queryByText("按此开辩")).toBeNull();
  });
});
