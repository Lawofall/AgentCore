// @vitest-environment jsdom
/**
 * CEO 处置动作的工具行标题：说清「撤的是谁」，不摆内部标识。
 *
 * 回归钉：`cancel_worker` / `resolve_escalation` 的参数是 run_id，标题曾直接拼成
 * `Cancel worker r-a3f2e1c8-…`——用户对不上协作图里的「研究员」，也就无从判断 CEO 这一手
 * 处置得对不对。`read_conversation` 同理：标题拼 conversation_id 不如亮出那场对话的标题。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import { type ExecutionPlan, useExecutionStore } from "@/stores/execution";
import type { ProcessStep } from "@/types/events";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: Object.assign(
    (selector: (s: { showBrowser: () => void }) => unknown) =>
      selector({ showBrowser: () => {} }),
    { getState: () => ({ showBrowser: () => {} }) },
  ),
}));

import { ToolLine } from "../ToolLine";

const MID = "msg-run-target";

const plan: ExecutionPlan = {
  id: "exec-1",
  planType: "multi_agent",
  taskSummary: "竞品调研",
  agents: [
    { id: "w1", role: "研究员" },
    { id: "w2", role: "撰写员" },
  ],
  runs: [
    { id: "r-a3f2e1c8-9b21", agentId: "w1", task: "调研", dependsOn: [] },
    { id: "r-77120c9a-4d10", agentId: "w2", task: "撰写", dependsOn: [] },
  ],
};

type ToolStep = Extract<ProcessStep, { kind: "tool" }>;

function step(over: Partial<ToolStep>): ToolStep {
  return {
    kind: "tool",
    id: "call_1",
    tool_name: "cancel_worker",
    arguments: {},
    result: "ok",
    display: null,
    status: "success",
    ...over,
  };
}

function renderLine(s: ToolStep, turnKey: string | undefined = MID) {
  return render(
    <TooltipProvider>
      <ToolLine step={s} turnKey={turnKey} />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  useExecutionStore.setState({ byId: {} });
  useExecutionStore.getState().startExecution(plan, MID);
});

afterEach(cleanup);

describe("工具行标题 · CEO 处置动作指的是谁", () => {
  it("撤回队员标题落角色名，不出现 run_id", () => {
    renderLine(
      step({
        tool_name: "cancel_worker",
        arguments: { run_id: "r-a3f2e1c8-9b21", reason: "方向跑偏" },
      }),
    );
    expect(screen.getByText("研究员")).toBeTruthy();
    expect(screen.queryByText(/r-a3f2e1c8/)).toBeNull();
  });

  it("裁决求助标题落角色名，裁决正文不进标题", () => {
    renderLine(
      step({
        tool_name: "resolve_escalation",
        arguments: {
          run_id: "r-77120c9a-4d10",
          answer: "按方案 B 继续，预算不变，先出提纲再展开细节。",
        },
      }),
    );
    expect(screen.getByText("撰写员")).toBeTruthy();
    expect(screen.queryByText(/r-77120c9a/)).toBeNull();
    expect(screen.queryByText(/按方案 B 继续/)).toBeNull();
  });

  it("图上查不到该节点时宁可不显示，也不退回摆 id", () => {
    renderLine(
      step({
        tool_name: "cancel_worker",
        arguments: { run_id: "r-deadbeef-0000" },
      }),
    );
    expect(screen.getByText("Cancel worker")).toBeTruthy();
    expect(screen.queryByText(/deadbeef/)).toBeNull();
  });

  it("历史回合没有协作图（无 turnKey）也不摆 id", () => {
    renderLine(
      step({
        tool_name: "cancel_worker",
        arguments: { run_id: "r-a3f2e1c8-9b21" },
      }),
      undefined,
    );
    expect(screen.getByText("Cancel worker")).toBeTruthy();
    expect(screen.queryByText(/r-a3f2e1c8/)).toBeNull();
  });

  it("CEO 直接按角色名点人时照显（那本就是用户认得的词）", () => {
    renderLine(
      step({
        tool_name: "cancel_worker",
        arguments: { run_id: "研究员" },
      }),
    );
    expect(screen.getByText("研究员")).toBeTruthy();
  });

  it("查阅历史对话：标题不拼 conversation_id，改亮出那场对话的标题", () => {
    renderLine(
      step({
        tool_name: "read_conversation",
        arguments: { conversation_id: "c-8f31ab02-77de" },
        display: {
          conversation_id: "c-8f31ab02-77de",
          title: "上次那场定价讨论",
          truncated: false,
        },
      }),
    );
    expect(screen.getByText(/上次那场定价讨论/)).toBeTruthy();
    expect(screen.queryByText(/c-8f31ab02/)).toBeNull();
  });
});
