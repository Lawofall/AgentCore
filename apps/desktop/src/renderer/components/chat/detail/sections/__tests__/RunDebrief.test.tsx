// @vitest-environment jsdom

import { DebriefSection } from "@/components/chat/detail/sections/RunDebrief";
import type { RunDebrief } from "@/types/events";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}));

const baseDebrief: RunDebrief = {
  summary: "交叉验证完成",
  key_points: ["共识：一周内需清晰立场"],
  assumptions: "争议事实以公开报道为准",
  next_steps: "若用户同意，建议开辩",
};

const motionCard = {
  motion: "品牌是否应立即终止与该代言人的联名合作",
  sides: [
    { key: "terminate", name: "立即终止方", stance: "应立刻切割止损" },
    { key: "hold", name: "冷静观望方", stance: "证据未定不宜仓促解约" },
  ],
  fact_pointers: ["#r1", "#r3", "notes/endorsement.md"],
  rationale: "法律风险与品牌声誉的取舍无法靠继续取证收敛。",
  form: "debate" as const,
};

function expandDebrief() {
  fireEvent.click(screen.getByRole("button", { name: "交接简报" }));
}

describe("DebriefSection", () => {
  it("defaults to a collapsed face: only 交接简报, body hidden", () => {
    render(<DebriefSection debrief={baseDebrief} />);
    const face = screen.getByRole("button", { name: "交接简报" });
    expect(face).toBeTruthy();
    expect(face.closest(".bg-muted")).toBeNull();
    expect(screen.queryByText("交叉验证完成")).toBeNull();
    expect(screen.queryByText("结论")).toBeNull();
    expect(screen.queryByText("关键要点")).toBeNull();
    expect(screen.queryByText("关键假设")).toBeNull();
    expect(screen.queryByText("建议下一步")).toBeNull();
    expect(screen.queryByText("命题卡")).toBeNull();
  });

  it("muted inset wraps expanded body only", () => {
    render(<DebriefSection debrief={baseDebrief} />);
    expandDebrief();
    const title = screen.getByText("交叉验证完成");
    const inset = title.closest(".bg-muted");
    expect(inset).toBeTruthy();
    expect(inset?.className).toContain("px-2.5");
    expect(inset?.className).toContain("py-1.5");
    expect(
      screen.getByRole("button", { name: "交接简报" }).closest(".bg-muted"),
    ).toBeNull();
  });

  it("expands to show summary then points / assumptions / next steps", () => {
    render(<DebriefSection debrief={baseDebrief} />);
    expandDebrief();
    expect(screen.getByText("交叉验证完成")).toBeTruthy();
    expect(screen.getByText("关键要点")).toBeTruthy();
    expect(screen.getByText("共识：一周内需清晰立场")).toBeTruthy();
    expect(screen.getByText("关键假设")).toBeTruthy();
    expect(screen.getByText("争议事实以公开报道为准")).toBeTruthy();
    expect(screen.getByText("建议下一步")).toBeTruthy();
    expect(screen.getByText("若用户同意，建议开辩")).toBeTruthy();
  });

  it("does not render leftover motion_card as 命题卡", () => {
    render(
      <DebriefSection debrief={{ ...baseDebrief, motion_card: motionCard }} />,
    );
    expect(screen.queryByText("命题卡")).toBeNull();
    expandDebrief();
    expect(screen.queryByText("命题卡")).toBeNull();
    expect(screen.queryByText("正反")).toBeNull();
    expect(screen.queryByText(motionCard.motion)).toBeNull();
  });

  it("degraded brief shows a notice and hides the body slice", () => {
    const degraded = {
      ...baseDebrief,
      degraded: true,
    } as RunDebrief;
    render(<DebriefSection debrief={degraded} />);
    expect(screen.getByText("简报由系统降级生成")).toBeTruthy();
    expect(screen.queryByText("交叉验证完成")).toBeNull();
    expect(screen.queryByText("关键要点")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("folds a long summary until 交接简报 is expanded", () => {
    const long =
      "新增 packages/core/src/tools 工具系统（ToolName/Tool 契约 + 9 真实工具实现 + createTool 工厂），并把 engine.setTool 接入为真实实例切换。";
    const { container } = render(
      <DebriefSection
        debrief={{
          ...baseDebrief,
          summary: long,
        }}
      />,
    );
    expect(screen.getByRole("button", { name: "交接简报" })).toBeTruthy();
    expect(screen.queryByText(long)).toBeNull();
    expect(container.querySelector(".line-clamp-2")).toBeNull();
    expect(screen.queryByText("关键要点")).toBeNull();
    expandDebrief();
    expect(screen.getByText(long)).toBeTruthy();
  });

  it("summary-only brief still folds under 交接简报", () => {
    render(<DebriefSection debrief={{ summary: "只写了结论" }} />);
    expect(screen.getByRole("button", { name: "交接简报" })).toBeTruthy();
    expect(screen.queryByText("只写了结论")).toBeNull();
    expandDebrief();
    expect(screen.getByText("只写了结论")).toBeTruthy();
  });
});
