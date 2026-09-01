// @vitest-environment jsdom
/**
 * 全屏画布不挂手册「?」：看这张图时不跳走读图例。
 * 图例仍从工具箱手册选读章进。
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { GraphToolbar } from "../GraphToolbar";

afterEach(cleanup);

describe("GraphToolbar", () => {
  it("全屏画布不挂「看手册说明」", () => {
    render(
      <TooltipProvider>
        <div className="relative h-20 w-40">
          <GraphToolbar layoutKind="leftright" onLayoutKindChange={() => {}} />
        </div>
      </TooltipProvider>,
    );
    expect(screen.queryByRole("button", { name: "看手册说明" })).toBeNull();
    expect(screen.getByRole("button", { name: "左右流" })).toBeTruthy();
  });
});
