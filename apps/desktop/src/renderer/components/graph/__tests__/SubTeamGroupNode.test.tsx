// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { type NodeProps, ReactFlowProvider } from "@xyflow/react";
import { afterEach, describe, expect, it } from "vitest";
import { SubTeamGroupNode } from "../SubTeamGroupNode";

afterEach(cleanup);

function renderGroup(data: {
  parentRole: string;
  memberCount: number;
  handleDirection: "horizontal" | "vertical";
  variant?: "debate";
}) {
  return render(
    <ReactFlowProvider>
      <SubTeamGroupNode
        {...({
          id: "g1",
          data,
          type: "subTeamGroup",
          selected: false,
          dragging: false,
          zIndex: 0,
          selectable: false,
          deletable: false,
          draggable: false,
          isConnectable: false,
          positionAbsoluteX: 0,
          positionAbsoluteY: 0,
        } as NodeProps)}
      />
    </ReactFlowProvider>,
  );
}

describe("SubTeamGroupNode", () => {
  it("paints a solid grouping wash, not a dashed near-white fill", () => {
    const { container } = renderGroup({
      parentRole: "渲染与性能工程师",
      memberCount: 3,
      handleDirection: "horizontal",
    });
    expect(screen.getByText("渲染与性能工程师 子队 · 3 人")).toBeTruthy();
    const box = container.querySelector(".h-full") as HTMLElement;
    expect(box.className).toContain("bg-muted-foreground/3");
    expect(box.className).not.toContain("border");
    expect(box.className).not.toContain("bg-muted/20");
  });

  it("leaves debate compounds unpainted", () => {
    const { container } = renderGroup({
      parentRole: "主持人",
      memberCount: 4,
      handleDirection: "horizontal",
      variant: "debate",
    });
    expect(screen.queryByText(/子队/)).toBeNull();
    const box = container.querySelector(".h-full") as HTMLElement;
    expect(box.className).not.toContain("border-border");
    expect(box.className).not.toContain("bg-muted-foreground/3");
  });
});
