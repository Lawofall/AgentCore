// @vitest-environment jsdom
/**
 * 队员详情：成功 handoff 工具行即简报卡；页脚 DebriefBlock 只留给没有成功 handoff 的降级简报。
 * 行脸与页脚跳过共用 handoffBrief 判定。
 */
import { TeamView } from "@/components/TeamView";
import type { RunToolCall } from "@/protocol/fold";
import type {
  ProjectedAgent,
  ProjectedRun,
  RunDebrief,
} from "@agentcore/protocol-conformance";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/Modal", () => ({
  Modal: ({ children, label }: { children: ReactNode; label?: string }) => (
    <div aria-label={label}>{children}</div>
  ),
}));

vi.mock("@/components/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}));

const HANDOFF_ACK = "已收尾并提交交接简报。";
const TOOL_SUMMARY = "交叉验证完成";
const FOOTER_SUMMARY = "页脚不该再出现的收获简报";

afterEach(cleanup);

function makeAgent(
  p: Partial<ProjectedAgent> & { id: string; role: string },
): ProjectedAgent {
  return {
    thinking: false,
    status: "completed",
    currentRunId: null,
    output: "",
    reasoning: "",
    toolProgress: null,
    ...p,
  };
}

function makeRun(p: Partial<ProjectedRun> & { id: string }): ProjectedRun {
  return {
    agentId: "a1",
    task: "调研竞品",
    status: "completed",
    dependsOn: [],
    outputSummary: null,
    debrief: null,
    durationMs: null,
    error: null,
    failureKind: null,
    productLanded: null,
    parentRunId: null,
    kind: "agent",
    role: "调研员",
    model: null,
    usage: null,
    cost: null,
    stance: null,
    group: null,
    round: 0,
    continuesRunId: null,
    revised: null,
    replacesRunId: null,
    checkpoint: null,
    receivedContext: [],
    escalations: [],
    process: [],
    actId: "act-1",
    ...p,
  };
}

const AGENTS = [makeAgent({ id: "a1", role: "调研员" })];

const HANDOFF_ARGS = {
  summary: TOOL_SUMMARY,
  key_points: ["共识：一周内需清晰立场"],
  assumptions: "争议事实以公开报道为准",
  next_steps: "若用户同意，建议开辩",
  motion_card: {
    motion: "该不该立刻开辩",
    sides: [],
    fact_pointers: [],
    rationale: "对立消不掉",
    form: "debate" as const,
  },
};

function successHandoff(
  args: Record<string, unknown> = HANDOFF_ARGS,
): RunToolCall {
  return {
    id: "h1",
    toolName: "handoff",
    arguments: args,
    result: HANDOFF_ACK,
    status: "success",
  };
}

function openMember(opts: {
  toolCalls?: RunToolCall[];
  debrief?: RunDebrief | null;
}) {
  const run = makeRun({
    id: "r1",
    debrief: opts.debrief === undefined ? null : opts.debrief,
  });
  render(
    <TeamView
      agents={AGENTS}
      runs={[run]}
      progress={{ completed: 1, total: 1 }}
      status="completed"
      runToolCalls={
        opts.toolCalls
          ? new Map<string, RunToolCall[]>([["r1", opts.toolCalls]])
          : undefined
      }
    />,
  );
  fireEvent.click(screen.getByText("调研员"));
}

describe("TeamView · handoff 简报卡", () => {
  it("成功 handoff 行是简报卡：peek=summary，不露 JSON / 协议回执", () => {
    openMember({
      toolCalls: [successHandoff()],
      debrief: { summary: FOOTER_SUMMARY, key_points: ["页脚要点"] },
    });

    expect(screen.getByText("Handoff")).toBeTruthy();
    expect(screen.getByText(TOOL_SUMMARY)).toBeTruthy();
    expect(screen.queryByText(HANDOFF_ACK)).toBeNull();
    expect(screen.queryByText(/"summary"/)).toBeNull();
    expect(screen.queryByText("Done")).toBeNull();
    expect(screen.queryByText("关键要点")).toBeNull();
  });

  it("有详情可展开，版式同 DebriefBlock；不补命题卡", () => {
    openMember({ toolCalls: [successHandoff()] });

    fireEvent.click(screen.getByRole("button", { name: /Handoff/ }));
    expect(screen.getByText("关键要点")).toBeTruthy();
    expect(screen.getByText("共识：一周内需清晰立场")).toBeTruthy();
    expect(screen.getByText("关键假设")).toBeTruthy();
    expect(screen.getByText("建议下一步")).toBeTruthy();
    expect(screen.queryByText("命题卡")).toBeNull();
    expect(screen.queryByText("该不该立刻开辩")).toBeNull();
  });

  it("summary-only 不展开", () => {
    openMember({
      toolCalls: [successHandoff({ summary: "只写了结论" })],
    });

    expect(screen.getByText("Handoff")).toBeTruthy();
    expect(screen.getByText("只写了结论")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Handoff/ })).toBeNull();
  });

  it("toolCalls 已有成功 handoff → 不渲染页脚 DebriefBlock", () => {
    openMember({
      toolCalls: [successHandoff()],
      debrief: { summary: FOOTER_SUMMARY, key_points: ["页脚要点"] },
    });

    expect(screen.queryByText("交接简报")).toBeNull();
    expect(screen.queryByText(FOOTER_SUMMARY)).toBeNull();
    expect(screen.queryByText("页脚要点")).toBeNull();
    expect(screen.getByText(TOOL_SUMMARY)).toBeTruthy();
  });

  it("失败 handoff 保持错误行", () => {
    openMember({
      toolCalls: [
        {
          id: "h1",
          toolName: "handoff",
          arguments: { summary: TOOL_SUMMARY },
          result: "简报校验失败",
          status: "error",
          failure: { message: "简报校验失败", code: "handoff_failed" },
        },
      ],
    });

    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.getByText("Handoff")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Handoff/ }));
    expect(screen.getByText("简报校验失败")).toBeTruthy();
    expect(screen.queryByText("交接简报")).toBeNull();
  });

  it("无成功 handoff 的 degraded debrief 仍走页脚+降级提示", () => {
    openMember({
      toolCalls: [
        {
          id: "s1",
          toolName: "web_search",
          arguments: { query: "竞品" },
          result: "ok",
          status: "success",
        },
      ],
      debrief: {
        summary: "正文切片不该露出",
        key_points: ["降级要点"],
        degraded: true,
      } as RunDebrief,
    });

    expect(screen.getByText("交接简报")).toBeTruthy();
    expect(screen.getByText("简报由系统降级生成")).toBeTruthy();
    expect(screen.queryByText("正文切片不该露出")).toBeNull();
    expect(screen.queryByText("降级要点")).toBeNull();
  });
});
