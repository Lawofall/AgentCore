// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GraphActionBar } from "../GraphActionBar";
import type { GraphPendingDecision } from "../pendingDecisions";

const DECISIONS: GraphPendingDecision[] = [
  {
    id: "esc:e1",
    kind: "escalation",
    runId: "r1",
    actId: "act-2",
    title: "调研",
    detail: "待你拍板（缺输入）",
  },
  {
    id: "appr:a1",
    kind: "approval",
    runId: "cap",
    actId: null,
    title: "工具审批",
    detail: "待放行",
  },
];

describe("GraphActionBar", () => {
  it("renders nothing when there are no pending decisions", () => {
    const { container } = render(
      <GraphActionBar decisions={[]} onLocate={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing for a single pending decision (方案 C：单项由拍板中心独占表达)", () => {
    const { container } = render(
      <GraphActionBar decisions={[DECISIONS[0]]} onLocate={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows the aggregate count and is keyboard reachable (a button)", () => {
    render(<GraphActionBar decisions={DECISIONS} onLocate={vi.fn()} />);
    const trigger = screen.getByRole("button", {
      name: "待你拍板 2 项，展开定位",
    });
    expect(trigger).toBeTruthy();
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("expands to list each decision and locates on click", () => {
    const onLocate = vi.fn();
    render(<GraphActionBar decisions={DECISIONS} onLocate={onLocate} />);
    fireEvent.click(
      screen.getByRole("button", { name: "待你拍板 2 项，展开定位" }),
    );
    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getByText("调研")).toBeTruthy();
    expect(screen.getByText("工具审批")).toBeTruthy();

    fireEvent.click(screen.getByRole("menuitem", { name: /调研/ }));
    expect(onLocate).toHaveBeenCalledTimes(1);
    expect(onLocate).toHaveBeenCalledWith(DECISIONS[0]);
    // Collapses after locating.
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
