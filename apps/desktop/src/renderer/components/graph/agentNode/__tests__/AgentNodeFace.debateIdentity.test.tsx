// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentNodeCardFace } from "../AgentNodeFace";
import { buildAgentNodePresentation } from "../presentation";
import type { AgentNodeData } from "../shared";

function debateNode(extra: Partial<AgentNodeData> = {}): AgentNodeData {
  return {
    agentId: "a-pro",
    role: "正方",
    runId: "r-pro",
    status: "running",
    isAnimating: true,
    task: "就「自主行为」立论",
    outputPreview: "",
    tokenCount: 0,
    toolCount: 0,
    focused: false,
    stance: "pro",
    group: "debate:debate",
    ...extra,
  };
}

describe("AgentNodeCardFace · debate identity", () => {
  it("omits default 正方 title and paints the pro stance token", () => {
    const d = debateNode();
    const p = buildAgentNodePresentation(d);
    render(
      <AgentNodeCardFace
        d={d}
        p={p}
        flashColor="var(--success)"
        flashing={false}
      />,
    );
    expect(screen.queryByText("正方")).toBeNull();
    expect(screen.getByText("正")).toBeTruthy();
    const glyph = screen.getByText("正");
    expect(
      glyph.getAttribute("style") ??
        glyph.parentElement?.getAttribute("style") ??
        "",
    ).toContain("--debate-side-pro");
  });

  it("keeps a custom side name as the title", () => {
    const d = debateNode({ role: "原告", task: "就争议焦点立论" });
    const p = buildAgentNodePresentation(d);
    render(
      <AgentNodeCardFace
        d={d}
        p={p}
        flashColor="var(--success)"
        flashing={false}
      />,
    );
    expect(screen.getByText("原告")).toBeTruthy();
    expect(screen.queryByText("正方")).toBeNull();
  });
});
