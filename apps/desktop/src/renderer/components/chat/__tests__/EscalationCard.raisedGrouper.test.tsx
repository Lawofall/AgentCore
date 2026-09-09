// @vitest-environment jsdom
/**
 * ≥2 条边干边上报的收纳行：过程行密度（贴字箭头），不是灰底分组卡。
 */
import { EscalationCards } from "@/components/chat/EscalationCard";
import type { Execution, RunEscalation } from "@/stores/execution";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const execSlot: { current: Execution | null } = { current: null };

vi.mock("@/stores/execution", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/execution")>();
  return {
    ...actual,
    useMessageExecution: () => execSlot.current,
  };
});

afterEach(() => {
  execSlot.current = null;
});

function raised(id: string, question: string): RunEscalation {
  return {
    id,
    question,
    assumption: "先按假设继续",
    blocking: false,
    status: "raised",
    answer: null,
    kind: "normal",
    questions: [],
  };
}

function execWithRaised(questions: [string, string]): Execution {
  return {
    agents: [
      { id: "a1", role: "审查员" },
      { id: "a2", role: "落盘员" },
    ],
    runs: [
      {
        id: "r1",
        agentId: "a1",
        escalations: [raised("e1", questions[0])],
      },
      {
        id: "r2",
        agentId: "a2",
        escalations: [raised("e2", questions[1])],
      },
    ],
  } as Execution;
}

describe("EscalationCards · raised grouper", () => {
  it("默认收成过程行：N 条边干边上报，无灰底、无展开套话", () => {
    execSlot.current = execWithRaised([
      "无法将第5轮审查报告落盘。",
      "目标文件被锁定。",
    ]);
    render(
      <EscalationCards messageId="msg-1" conversationId="conv-1" interactive />,
    );

    const face = screen.getByRole("button", { name: "2 条边干边上报" });
    expect(face.getAttribute("aria-expanded")).toBe("false");
    expect(face.className).toContain("w-auto");
    expect(face.className).toContain("text-sm");
    expect(face.className.split(/\s+/)).not.toContain("w-full");
    expect(face.className).not.toContain("bg-card");
    expect(
      face.lastElementChild?.classList.contains("lucide-chevron-right"),
    ).toBe(true);
    expect(screen.queryByText(/展开/)).toBeNull();
    expect(screen.queryByText(/收起/)).toBeNull();
    expect(screen.queryByText(/无法将第5轮审查报告落盘/)).toBeNull();
    expect(screen.queryByText(/目标文件被锁定/)).toBeNull();
  });

  it("点开后列出单条，收纳行仍是同一句", () => {
    execSlot.current = execWithRaised([
      "无法将第5轮审查报告落盘。",
      "目标文件被锁定。",
    ]);
    render(
      <EscalationCards messageId="msg-1" conversationId="conv-1" interactive />,
    );

    fireEvent.click(screen.getByRole("button", { name: "2 条边干边上报" }));
    const face = screen.getByRole("button", { name: "2 条边干边上报" });
    expect(face.getAttribute("aria-expanded")).toBe("true");
    expect(screen.queryByText(/展开/)).toBeNull();
    expect(screen.queryByText(/收起/)).toBeNull();
    expect(screen.getByText("审查员 · 边干边上报")).toBeTruthy();
    expect(screen.getByText("落盘员 · 边干边上报")).toBeTruthy();
  });
});
