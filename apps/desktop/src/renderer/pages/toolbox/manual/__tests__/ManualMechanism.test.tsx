// @vitest-environment jsdom
import { ManualMechanism } from "@/pages/toolbox/manual/ManualMechanism";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

describe("ManualMechanism", () => {
  it("renders content-driven sections with stable deep-link ids", () => {
    render(
      <MemoryRouter initialEntries={["/toolbox/manual/mechanism"]}>
        <ManualMechanism />
      </MemoryRouter>,
    );
    expect(screen.getByText("看图跑一遍")).toBeTruthy();
    expect(screen.getByText("看懂协作图")).toBeTruthy();
    expect(screen.getByText("从发消息到收答案")).toBeTruthy();
    expect(screen.getByText("机制场景")).toBeTruthy();
    expect(screen.queryByText("运行时全景")).toBeNull();
    expect(screen.queryByText("协作回合")).toBeNull();

    for (const id of ["live", "legend", "panorama", "scenarios"]) {
      expect(document.getElementById(id)).toBeTruthy();
    }
    expect(document.getElementById("turnflow")).toBeNull();

    expect(screen.getByText("接单准备")).toBeTruthy();
    expect(screen.getByText("分工推进")).toBeTruthy();
    expect(screen.getByText("收尾交付")).toBeTruthy();
    expect(screen.getByText("你说出目标")).toBeTruthy();
    expect(screen.getByText("最后一段封口")).toBeTruthy();
    expect(screen.getByText("中途你会看见的真界面")).toBeTruthy();
    expect(screen.getByText(/不看不影响使用/)).toBeTruthy();
    expect(screen.queryByText(/WaveScheduler/)).toBeNull();
    expect(screen.queryByText(/finish_reason/)).toBeNull();
    expect(screen.queryByText(/ReAct/)).toBeNull();
    expect(screen.queryByText(/depends_on/)).toBeNull();
    expect(screen.queryByText(/max_parallel/)).toBeNull();

    const embeds = Array.from(
      document.querySelectorAll("[data-manual-embed]"),
    ).map((el) => el.getAttribute("data-manual-embed"));
    expect(embeds).toEqual([
      "HeroGraph",
      "GraphLegend",
      "ManualCheckpointCardPreview",
      "ManualApprovalCardPreview",
      "ManualDebateScoreboardPreview",
      "ManualDebateFinalePreview",
      "MechanismScenarios",
    ]);
  });
});
