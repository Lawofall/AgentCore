// @vitest-environment jsdom
/**
 * 拍板卡不挂手册「?」：挡路冻结时跳走读手册是错时机。
 * 手册仍从工具箱 / 命令面板进；辩论室 / 协作图 / 审批保留现场 `?`。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import type { AskUiIntent } from "@/lib/checkpointIntent";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AskUserCard } from "../CheckpointCard";

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

afterEach(cleanup);

const INTENTS: AskUiIntent[] = ["decision", "organize_plan", "daily_review"];

describe("AskUserCard manual help", () => {
  it.each(INTENTS)("%s 拍板卡不挂「看手册说明」", (intent) => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <AskUserCard
            content={{
              question: "选 A 还是 B？",
              assumptions: [],
              questions: [],
            }}
            intent={intent}
            onSubmit={() => {}}
          />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(screen.queryByRole("button", { name: "看手册说明" })).toBeNull();
  });
});
