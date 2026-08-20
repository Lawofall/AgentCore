// @vitest-environment jsdom
/**
 * 检查点拍板卡（AskUserCard）挂手册「?」入口（深链 collaboration?s=checkpoint）。
 */

import { MANUAL_HELP } from "@/components/ManualHelpLink";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AskUserCard } from "../CheckpointCard";

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

afterEach(cleanup);

describe("AskUserCard manual help", () => {
  it("decision 拍板卡挂「看手册说明」入口", () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <AskUserCard
            content={{
              question: "选 A 还是 B？",
              assumptions: [],
              questions: [],
            }}
            intent="decision"
            onSubmit={() => {}}
          />
        </TooltipProvider>
      </MemoryRouter>,
    );
    const btn = screen.getByRole("button", { name: "看手册说明" });
    expect(btn.getAttribute("data-manual-help")).toBe(MANUAL_HELP.checkpoint);
  });
});
