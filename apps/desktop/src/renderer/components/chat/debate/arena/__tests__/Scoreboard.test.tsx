// @vitest-environment jsdom
/**
 * 辩论室记分牌页头挂手册「?」入口（深链 collaboration?s=debate）。
 */

import { MANUAL_HELP } from "@/components/ManualHelpLink";
import { TooltipProvider } from "@/components/ui/tooltip";
import { DEMO_DEBATE_MODEL } from "@/pages/toolbox/manual/embeds/demoDebate";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DebateModel } from "../../model";
import { Scoreboard } from "../Scoreboard";

vi.mock("@/components/chat/debate/ModelBadge", () => ({
  ModelBadge: () => null,
}));

function makeModel(overrides: Partial<DebateModel> = {}): DebateModel {
  return {
    form: "debate",
    motion: "是否采用方案 A",
    stopReason: null,
    moderatorRunId: null,
    narrativeFirst: false,
    rounds: [],
    brief: null,
    sides: null,
    closings: [],
    opening: null,
    settled: true,
    ...overrides,
  } as DebateModel;
}

afterEach(cleanup);

describe("Scoreboard manual help", () => {
  it("挂「看手册说明」入口，深链到辩论节", () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <Scoreboard model={makeModel()} onScrollTo={() => {}} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    const btn = screen.getByRole("button", { name: "看手册说明" });
    expect(btn.getAttribute("data-manual-help")).toBe(MANUAL_HELP.debate);
  });

  it("正反行只留双方身份，不展示比分、站队、掌舵、动量图", () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <Scoreboard model={DEMO_DEBATE_MODEL} onScrollTo={() => {}} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(screen.getAllByText("加速派").length).toBeGreaterThan(0);
    expect(screen.getAllByText("审慎派").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "掌舵" })).toBeNull();
    expect(screen.queryByText("你站")).toBeNull();
    expect(screen.queryByLabelText("动量图例")).toBeNull();
    expect(screen.queryByRole("button", { name: /净分构成/ })).toBeNull();
  });

  it("这场怎么读：不用你收场", () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <Scoreboard model={DEMO_DEBATE_MODEL} onScrollTo={() => {}} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "这场怎么读" }));
    expect(screen.getByText(/不用你收场/)).toBeTruthy();
  });
});
