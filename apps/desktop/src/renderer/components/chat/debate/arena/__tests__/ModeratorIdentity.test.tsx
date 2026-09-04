// @vitest-environment jsdom
/**
 * 主持人身份壳：法槌 + 「主持人」；模型徽章只挂记分牌。
 */

import type { RunNode } from "@/stores/execution";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { DebateCrossExamView } from "../../model";
import { CrossExamSection } from "../CrossExamSection";
import { JudgeNote } from "../JudgeNote";
import { ModeratorIdentity, resolveModeratorModel } from "../ModeratorIdentity";
import { OpeningNote } from "../OpeningNote";

afterEach(() => {
  cleanup();
});

describe("ModeratorIdentity", () => {
  it("渲染法槌 + 主持人，不挂厂商徽章", () => {
    const { container } = render(<ModeratorIdentity />);
    expect(screen.getByText("主持人")).toBeTruthy();
    expect(container.querySelector("svg")).toBeTruthy();
    expect(screen.queryByText("DeepSeek")).toBeNull();
  });
});

describe("resolveModeratorModel", () => {
  it("直播态 moderatorRunId 为空 → null", () => {
    expect(
      resolveModeratorModel({ moderatorRunId: null }, { runs: [] }),
    ).toBeNull();
  });

  it("收场后从 execution.runs 取 model", () => {
    const run = {
      id: "moderator",
      model: "deepseek/deepseek-v4-flash",
    } as unknown as RunNode;
    expect(
      resolveModeratorModel({ moderatorRunId: "moderator" }, { runs: [run] }),
    ).toBe("deepseek/deepseek-v4-flash");
  });
});

describe("OpeningNote 主持人入场", () => {
  it("身份壳 + 原文，不挂模型徽章", () => {
    render(<OpeningNote text="今天我们讨论方案 A。" />);
    expect(screen.getByText("主持人")).toBeTruthy();
    expect(screen.getByText("今天我们讨论方案 A。")).toBeTruthy();
    expect(screen.queryByText("DeepSeek")).toBeNull();
  });
});

describe("JudgeNote 完成态身份", () => {
  it("完成态含「主持人」字样与法槌", () => {
    const { container } = render(<JudgeNote text="正方本轮更扎实。" />);
    expect(screen.getByText("主持人")).toBeTruthy();
    expect(screen.getByText("正方本轮更扎实。")).toBeTruthy();
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("加载态仍含「主持人」", () => {
    render(<JudgeNote text="" pending />);
    expect(screen.getByText("主持人")).toBeTruthy();
    expect(screen.getByText("正在小结…")).toBeTruthy();
  });

  it("拟质询 pending 文案", () => {
    render(<JudgeNote text="" pending pendingKind="cross_exam" />);
    expect(screen.getByText("主持人正在拟质询…")).toBeTruthy();
    expect(screen.queryByText("正在小结…")).toBeNull();
  });

  it("小结 pending 文案（显式 summary）", () => {
    render(<JudgeNote text="" pending pendingKind="summary" />);
    expect(screen.getByText("正在小结…")).toBeTruthy();
  });
});

describe("CrossExamSection 质询报幕", () => {
  it("报幕含法槌身份壳 + 必答质询文案，不挂模型徽章", () => {
    const cx: DebateCrossExamView = {
      targetKey: "pro",
      stance: null,
      targetName: "支持方",
      targetColorVar: "var(--debate-pro)",
      exchanges: [],
      answerRun: null,
    };
    const { container } = render(
      <CrossExamSection exchanges={[cx]} messageId="m1" sceneKey="m1:cx" />,
    );

    expect(screen.getByText("质询")).toBeTruthy();
    expect(screen.getByText("主持人")).toBeTruthy();
    expect(screen.getByText("发出必答质询")).toBeTruthy();
    expect(screen.queryByText("DeepSeek")).toBeNull();
    expect(container.querySelector("svg")).toBeTruthy();
  });
});
