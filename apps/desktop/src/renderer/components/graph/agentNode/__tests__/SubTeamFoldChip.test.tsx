import { NODE_HEIGHT } from "@agentcore/graph-layout";
// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentNodeCardFace } from "../AgentNodeFace";
import { SubTeamFoldChip } from "../SubTeamFoldChip";
import { buildAgentNodePresentation } from "../presentation";
import type { AgentNodeData } from "../shared";
import { FACE_CARD_HEIGHT } from "../shared";

function baseData(overrides: Partial<AgentNodeData> = {}): AgentNodeData {
  return {
    agentId: "a1",
    role: "研发",
    runId: "r1",
    status: "completed",
    isAnimating: false,
    task: "实现折叠 chip",
    outputPreview: "",
    tokenCount: 0,
    toolCount: 0,
    focused: false,
    foldedChildCount: 3,
    unitExpanded: false,
    onToggleUnitExpand: vi.fn(),
    ...overrides,
  };
}

describe("SubTeamFoldChip", () => {
  it("shows count with expand a11y when collapsed", () => {
    const onToggle = vi.fn();
    render(
      <div className="relative">
        <SubTeamFoldChip
          count={3}
          expanded={false}
          horizontal
          onToggle={onToggle}
        />
      </div>,
    );
    const btn = screen.getByRole("button", { name: "展开子队（3）" });
    expect(btn.getAttribute("title")).toBe("展开子队（3）");
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(btn.textContent).toMatch(/3/);
    fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("shows collapse a11y when expanded and stops click propagation", () => {
    const onToggle = vi.fn();
    const parentClick = vi.fn();
    render(
      // biome-ignore lint/a11y/useKeyWithClickEvents: test host only
      <div className="relative" onClick={parentClick}>
        <SubTeamFoldChip
          count={2}
          expanded
          horizontal={false}
          onToggle={onToggle}
        />
      </div>,
    );
    const btn = screen.getByRole("button", { name: "收起子队（2）" });
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(parentClick).not.toHaveBeenCalled();
  });
});

describe("AgentNodeCardFace · no in-card fold pill", () => {
  it("keeps FACE_CARD_HEIGHT and omits expand/collapse pill text", () => {
    expect(FACE_CARD_HEIGHT).toBe(NODE_HEIGHT);
    const d = baseData();
    const p = buildAgentNodePresentation(d);
    render(
      <AgentNodeCardFace
        d={d}
        p={p}
        flashColor="var(--success)"
        flashing={false}
      />,
    );
    expect(screen.queryByText(/展开子队/)).toBeNull();
    expect(screen.queryByText(/收起子队/)).toBeNull();
    const card = screen.getByRole("button", { name: p.ariaLabel });
    expect(card.style.height).toBe(`${FACE_CARD_HEIGHT}px`);
  });
});
