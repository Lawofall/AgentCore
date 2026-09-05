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
      name: /渲染与几何层审查员 · 边干边上报（无需你拍板）/,
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText(/无法将第5轮审查报告落盘/)).toBeNull();
    expect(screen.queryByText(/暂定假设/)).toBeNull();

    fireEvent.click(toggle);
    expect(
      screen
        .getByRole("button", {
          name: /渲染与几何层审查员 · 边干边上报（无需你拍板）/,
        })
        .getAttribute("aria-expanded"),
    ).toBe("true");
    expect(screen.getByText(/请授予写盘或由主管代为持久化/)).toBeTruthy();
    expect(
      screen.getByText(
        "暂定假设：主管将据正文内容持久化报告或于下波授予写盘工具",
      ),
    ).toBeTruthy();
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
      name: /落盘员 · 卡住早停（交付可能不完整）/,
    });
    expect(screen.queryByRole("button", { name: /边干边上报/ })).toBeNull();

    fireEvent.click(toggle);
    expect(screen.getByText(/校验反复失败，已早停以免空转/)).toBeTruthy();
    expect(screen.queryByText(/边干边上报/)).toBeNull();
    expect(screen.queryByText(/已按假设继续/)).toBeNull();
    expect(screen.queryByText(/暂定假设/)).toBeNull();
    expect(screen.queryByText(/无需你拍板/)).toBeNull();
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
        name: /审查员 · 卡住早停（交付可能不完整）/,
      }),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /边干边上报/ })).toBeNull();
  });
});

describe("EscalationCard · resolved collapse", () => {
  it("已答复默认收起，展开后可见问题与灰底答复", () => {
    render(
      <EscalationCard
        escalation={resolvedEsc()}
        conversationId="conv-1"
        interactive
        {...{ role: "文档合并员" }}
      />,
    );

    const toggle = screen.getByRole("button", {
      name: /文档合并员 · 已答复/,
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText(/无法 file_write 落位/)).toBeNull();
    expect(screen.queryByText(/移交写权，继续落位/)).toBeNull();

    fireEvent.click(toggle);
    expect(
      screen
        .getByRole("button", { name: /文档合并员 · 已答复/ })
        .getAttribute("aria-expanded"),
    ).toBe("true");
    expect(screen.getByText(/请移交写权或改路径/)).toBeTruthy();
    expect(screen.getByText(/移交写权，继续落位 v1\.2 定稿/)).toBeTruthy();
    expect(screen.queryByText(/我的答复/)).toBeNull();
  });
});
