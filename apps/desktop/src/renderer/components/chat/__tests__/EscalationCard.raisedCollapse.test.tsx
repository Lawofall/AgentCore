// @vitest-environment jsdom
/**
 * 边干边上报 / 已答复：对齐 ResolvedDecisionRecord（默认收起，点开全文）。
 */
import { EscalationCard } from "@/components/chat/EscalationCard";
import type { RunEscalation } from "@/stores/execution";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

function raisedEsc(overrides: Partial<RunEscalation> = {}): RunEscalation {
  return {
    id: "esc-raised",
    question:
      "本轮工具清单未包含 file_write，无法将第5轮审查报告落盘。\n请授予写盘或由主管代为持久化。",
    assumption: "主管将据正文内容持久化报告或于下波授予写盘工具",
    blocking: false,
    status: "raised",
    answer: null,
    kind: "dep",
    questions: [],
    ...overrides,
  };
}

function resolvedEsc(overrides: Partial<RunEscalation> = {}): RunEscalation {
  return {
    id: "esc-resolved",
    question:
      "目标文件被其他 run 锁定，无法 file_write 落位 v1.2。\n请移交写权或改路径。",
    assumption: "保持原主，跳过该路径修订",
    blocking: true,
    status: "resolved",
    answer: "我的答复：\n· 是否移交写权：移交写权，继续落位 v1.2 定稿",
    kind: "normal",
    questions: [],
    arbitrated_by: "user",
    ...overrides,
  };
}

describe("EscalationCard · raised collapse", () => {
  it("默认收起为一行结论，点击可展开全文与暂定假设", () => {
    // Spread `role` — prop is teammate display name, not ARIA role (biome a11y).
    render(
      <EscalationCard
        escalation={raisedEsc()}
        conversationId="conv-1"
        interactive
        {...{ role: "渲染与几何层审查员" }}
      />,
    );

    const toggle = screen.getByRole("button", {
      name: /渲染与几何层审查员 · 边干边上报/,
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    const summary = screen.getByText(/渲染与几何层审查员 · 边干边上报/);
    expect(summary.className).toContain("text-sm");
    expect(summary.className).not.toContain("text-xs");
    expect(summary.className).not.toContain("font-medium");
    expect(screen.queryByText(/无需你拍板/)).toBeNull();
    expect(screen.queryByText(/无法将第5轮审查报告落盘/)).toBeNull();
    expect(screen.queryByText(/暂定假设/)).toBeNull();

    fireEvent.click(toggle);
    expect(
      screen
        .getByRole("button", {
          name: /渲染与几何层审查员 · 边干边上报/,
        })
        .getAttribute("aria-expanded"),
    ).toBe("true");
    expect(screen.getByText(/请授予写盘或由主管代为持久化/)).toBeTruthy();
    expect(
      screen.getByText(
        "暂定假设：主管将据正文内容持久化报告或于下波授予写盘工具",
      ).className,
    ).toContain("text-sm");
    expect(
      screen.getByText(
        "暂定假设：主管将据正文内容持久化报告或于下波授予写盘工具",
      ).className,
    ).not.toContain("text-xs");
    expect(screen.queryByText(/已按假设继续/)).toBeNull();
  });

  it("卡住早停 source：标题含卡住早停，无边干边上报/已按假设继续", () => {
    render(
      <EscalationCard
        escalation={raisedEsc({
          source: "validation_thrash",
          assumption: "",
          question: "校验反复失败，已早停以免空转。",
        })}
        conversationId="conv-1"
        interactive
        {...{ role: "落盘员" }}
      />,
    );

    const toggle = screen.getByRole("button", {
      name: /落盘员 · 卡住早停/,
    });
    expect(screen.queryByRole("button", { name: /边干边上报/ })).toBeNull();

    fireEvent.click(toggle);
    expect(screen.getByText(/校验反复失败，已早停以免空转/)).toBeTruthy();
    expect(screen.queryByText(/边干边上报/)).toBeNull();
    expect(screen.queryByText(/已按假设继续/)).toBeNull();
    expect(screen.queryByText(/暂定假设/)).toBeNull();
    expect(screen.queryByText(/无需你拍板/)).toBeNull();
    expect(screen.queryByText(/交付可能不完整/)).toBeNull();
  });

  it("ceiling_backstop 同样走卡住早停卡", () => {
    render(
      <EscalationCard
        escalation={raisedEsc({ source: "ceiling_backstop" })}
        conversationId="conv-1"
        interactive
        {...{ role: "审查员" }}
      />,
    );
    expect(
      screen.getByRole("button", {
        name: /审查员 · 卡住早停/,
      }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /边干边上报/ })).toBeNull();
  });
});

describe("EscalationCard · resolved collapse", () => {
  it("已答复默认收起只露结论，展开后见角色、题干与全文", () => {
    render(
      <EscalationCard
        escalation={resolvedEsc()}
        conversationId="conv-1"
        interactive
        {...{ role: "文档合并员" }}
      />,
    );

    const toggle = screen.getByRole("button", {
      name: /移交写权，继续落位 v1\.2 定稿/,
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.querySelector(".lucide-megaphone")).toBeTruthy();
    expect(toggle.querySelector(".lucide-check")).toBeNull();
    expect(screen.queryByText("已答复")).toBeNull();
    expect(screen.queryByText("文档合并员")).toBeNull();
    expect(screen.queryByText(/无法 file_write 落位/)).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("文档合并员")).toBeTruthy();
    expect(screen.getByText(/请移交写权或改路径/)).toBeTruthy();
    expect(
      screen.getByText(/是否移交写权：移交写权，继续落位 v1\.2 定稿/),
    ).toBeTruthy();
    expect(screen.queryByText("已答复")).toBeNull();
    expect(screen.queryByText(/我的答复/)).toBeNull();
  });

  it("按假设继续用时钟，展开假设与过程行同档字号", () => {
    render(
      <EscalationCard
        escalation={resolvedEsc({
          status: "assumed",
          answer: null,
          assumption: "保持原主，跳过该路径修订",
        })}
        conversationId="conv-1"
        interactive
        {...{ role: "文档合并员" }}
      />,
    );
    const toggle = screen.getByRole("button", {
      name: /文档合并员 · 你选了按假设继续/,
    });
    expect(toggle.querySelector(".lucide-clock")).toBeTruthy();
    fireEvent.click(toggle);
    const assumption = screen.getByText(/按假设继续：保持原主/);
    expect(assumption.className).toContain("text-sm");
    expect(assumption.className).not.toContain("text-xs");
  });
});

describe("EscalationCard · dormant", () => {
  it("回合结束后的未答卡收成过程行，不是拍板卡", () => {
    render(
      <EscalationCard
        escalation={{
          id: "esc-dormant",
          question: "是否移交写权？",
          assumption: "保持原主",
          blocking: true,
          status: "pending",
          answer: null,
          kind: "normal",
          questions: [],
        }}
        conversationId="conv-1"
        interactive={false}
        {...{ role: "文档合并员" }}
      />,
    );
    const toggle = screen.getByRole("button", {
      name: "文档合并员 · 曾请你拍板",
    });
    expect(toggle.querySelector(".lucide-megaphone")).toBeTruthy();
    expect(screen.queryByText(/本回合已结束/)).toBeNull();
    expect(screen.queryByText(/是否移交写权/)).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByText("是否移交写权？")).toBeTruthy();
    const assumption = screen.getByText("暂定假设：保持原主");
    expect(assumption.className).toContain("text-sm");
    expect(assumption.className).not.toContain("text-xs");
  });
});
