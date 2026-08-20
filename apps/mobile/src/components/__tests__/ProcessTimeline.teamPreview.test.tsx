// @vitest-environment jsdom
import { ProcessTimeline } from "@/components/ProcessTimeline";
import type { TeamPreviewTrace } from "@/protocol/teamPreviewTraces";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

describe("ProcessTimeline · team_preview traces", () => {
  it("pending 不占时间线", () => {
    const traces = new Map<string, TeamPreviewTrace>([
      [
        "tp1",
        {
          status: "pending",
          primitive: "delegate",
          note: "",
          headline: "",
          workerCount: 2,
          sideCount: 0,
          excludedCount: 0,
          tightenedCount: 0,
          label: "",
        },
      ],
    ]);
    const { container } = render(
      <ProcessTimeline
        steps={[{ kind: "team_preview", checkpoint_id: "tp1" }]}
        teamPreviewTraces={traces}
      />,
    );
    expect(screen.queryByTestId("team-preview-trace")).toBeNull();
    expect(screen.queryByTestId("timeline-missing-card")).toBeNull();
    expect(container.textContent).not.toContain("已调整");
  });

  it("resolved adjust 画已交回修订 + 意见原文", () => {
    const traces = new Map<string, TeamPreviewTrace>([
      [
        "tp1",
        {
          status: "resolved",
          primitive: "delegate",
          decision: "adjust",
          note: "改成两人，先做竞品",
          headline: "",
          workerCount: 2,
          sideCount: 0,
          excludedCount: 0,
          tightenedCount: 0,
          label: "已调整 · 已交回修订 · 预计 2 人开工",
        },
      ],
    ]);
    render(
      <ProcessTimeline
        steps={[{ kind: "team_preview", checkpoint_id: "tp1" }]}
        teamPreviewTraces={traces}
      />,
    );
    const row = screen.getByTestId("team-preview-trace");
    expect(row.getAttribute("data-decision")).toBe("adjust");
    expect(row.textContent).toContain("已调整 · 已交回修订 · 预计 2 人开工");
    expect(row.textContent).toContain("改成两人，先做竞品");
  });
});
