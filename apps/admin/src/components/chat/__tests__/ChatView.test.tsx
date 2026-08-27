// @vitest-environment jsdom
import { ChatView } from "@/components/chat/ChatView";
import type { ProjectedTurn } from "@agentcore/protocol-conformance";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(() => {
  cleanup();
});

function turn(partial: Partial<ProjectedTurn> = {}): ProjectedTurn {
  return {
    status: "completed",
    finishReason: "end_turn",
    outcome: "ok",
    error: null,
    content: "终态正文",
    reasoning: "",
    captainContext: [],
    process: [{ kind: "content", text: "过程正文" }],
    citations: [],
    evidenceLedger: [],
    citedIds: [],
    agents: [],
    runs: [
      {
        id: "r1",
        agentId: "researcher",
        task: "查资料",
        status: "completed",
        dependsOn: [],
        outputSummary: "不该出现的产出",
        debrief: { summary: "不该出现的复盘" },
        durationMs: null,
        error: "不该出现的节点错误",
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
        actId: "act-1",
        checkpoint: null,
        receivedContext: [],
        escalations: [],
        process: [],
      },
    ],
    acts: [],
    progress: { completed: 1, total: 1 },
    interactions: [
      {
        kind: "approval",
        id: "a1",
        status: "pending",
        toolCallId: "t1",
        toolName: "bash",
        arguments: {},
      },
    ],
    cost: null,
    debate: null,
    debateRounds: [],
    debatePretrial: null,
    crossExamEnabled: false,
    debateOpening: null,
    teamSynthesisPreview: null,
    deliveryStatus: null,
    turnWarning: null,
    autoFolder: null,
    teamNotes: [],
    userInterjections: [],
    ...partial,
  };
}

describe("ChatView", () => {
  it("renders a multi-agent projected turn", () => {
    render(<ChatView content="终态正文" projected={turn()} />);
    expect(screen.getByLabelText("对话终态")).toBeTruthy();
    expect(screen.getAllByText("终态正文").length).toBeGreaterThan(0);
    expect(screen.queryByText("协作已完成")).toBeNull();
    expect(screen.getByText("1/1")).toBeTruthy();
    expect(screen.getByText("调研员")).toBeTruthy();
    expect(screen.getByText("查资料")).toBeTruthy();
    expect(screen.getByText("审批")).toBeTruthy();
    expect(screen.getByText("pending")).toBeTruthy();
    expect(screen.queryByText("过程正文")).toBeNull();
    expect(screen.queryByText("助手")).toBeNull();
    expect(screen.queryByText("outcome ok")).toBeNull();
    expect(screen.queryByText("finish end_turn")).toBeNull();
    expect(screen.queryByText("不该出现的产出")).toBeNull();
    expect(screen.queryByText("不该出现的复盘")).toBeNull();
    expect(screen.queryByText("不该出现的节点错误")).toBeNull();
  });

  it("keeps projected.process when runs_payload.process is []", () => {
    render(
      <ChatView
        content="x"
        projected={turn({
          process: [{ kind: "reasoning", text: "完整思考" }],
        })}
        runsPayload={{ process: [] }}
      />,
    );
    expect(screen.queryByText("完整思考")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^思考$/ }));
    expect(screen.getByText("完整思考")).toBeTruthy();
    expect(screen.getByText("完整思考").closest(".overflow-auto")?.className).toMatch(
      /text-muted-foreground/,
    );
  });

  it("does not crash on a sparse production projected dict", () => {
    render(
      <ChatView
        content="松散投影"
        projected={{ status: "completed" }}
      />,
    );
    expect(screen.getByLabelText("对话终态")).toBeTruthy();
    expect(screen.getByText("松散投影")).toBeTruthy();
    expect(screen.queryByText("completed")).toBeNull();
    expect(screen.queryByLabelText("来源")).toBeNull();
    expect(screen.queryByLabelText("团队")).toBeNull();
  });

  it("folds thought, tools, and sources until clicked", () => {
    render(
      <ChatView
        content="综合来看"
        projected={turn({
          process: [
            { kind: "reasoning", text: "先查资料。" },
            {
              kind: "tool",
              id: "tc1",
              tool_name: "web_search",
              arguments: { query: "AgentCore 架构" },
              result: "找到来源。",
              status: "success",
            },
          ],
          citations: [
            {
              url: "https://a.example/x",
              title: "来源 A",
              snippet: "片段 A",
              site: "a.example",
              id: "#r1",
              tier: "unknown",
            },
          ],
          interactions: [
            {
              kind: "approval",
              id: "a1",
              status: "resolved",
              toolCallId: "t1",
              toolName: "bash",
              arguments: {},
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("思考 1 步 · 使用 1 个工具")).toBeTruthy();
    expect(screen.queryByText("先查资料。")).toBeNull();
    expect(screen.queryByText("web_search")).toBeNull();
    expect(screen.queryByLabelText("工具参数")).toBeNull();
    expect(screen.queryByText("片段 A")).toBeNull();
    expect(screen.getByText("来源 A")).toBeTruthy();
    expect(screen.getByText("来源 1")).toBeTruthy();
    expect(screen.getByText("审批")).toBeTruthy();
    expect(screen.getByText("resolved")).toBeTruthy();
    expect(screen.queryByText(/通过|拒绝/)).toBeNull();
    expect(screen.getByLabelText("对话终态").querySelector("article")?.className).toMatch(
      /min-w-0/,
    );

    fireEvent.click(screen.getByText("思考 1 步 · 使用 1 个工具"));
    expect(screen.getByText("web_search")).toBeTruthy();
    expect(screen.getByRole("button", { name: /^思考$/ })).toBeTruthy();
    expect(screen.queryByText("先查资料。")).toBeNull();
    expect(screen.queryByLabelText("工具参数")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^思考$/ }));
    expect(screen.getByText("先查资料。")).toBeTruthy();
    expect(screen.getByText("先查资料。").closest(".overflow-auto")?.className).toMatch(
      /text-muted-foreground/,
    );

    fireEvent.click(screen.getByText("web_search"));
    expect(screen.getByLabelText("工具参数").textContent).toContain(
      "AgentCore 架构",
    );
    expect(screen.getByLabelText("工具结果").textContent).toContain("找到来源。");
    expect(screen.getByLabelText("工具结果").className).toMatch(/max-w-full/);
    expect(screen.getByLabelText("工具结果").className).toMatch(/max-h-48/);

    fireEvent.click(screen.getByText("来源 1"));
    expect(screen.getAllByText("来源 A").length).toBeGreaterThan(0);
    expect(screen.getByText("片段 A")).toBeTruthy();
  });

  it("clamps a huge tool dump so it cannot inflate the column", () => {
    const tail = "UNIQUE_TAIL";
    const huge = `START${"x".repeat(8000)}${tail}`;
    render(
      <ChatView
        content="综合来看"
        projected={turn({
          process: [
            {
              kind: "tool",
              id: "tc1",
              tool_name: "grep",
              arguments: { pattern: "foo" },
              result: huge,
              status: "success",
            },
          ],
          interactions: [],
          runs: [],
        })}
      />,
    );
    fireEvent.click(screen.getByText("使用 1 个工具"));
    fireEvent.click(screen.getByText("grep"));
    const result = screen.getByLabelText("工具结果");
    expect(result.textContent).toContain("START");
    expect(result.textContent).not.toContain(tail);
    expect(screen.getByText("已截断")).toBeTruthy();
  });

  it("stays non-blank when projected is null (process-only row)", () => {
    render(
      <ChatView
        content="只靠正文"
        projected={null}
        runsPayload={{
          finish_reason: "end_turn",
          process: [{ kind: "tool", tool_name: "web_search", status: "success" }],
        }}
      />,
    );
    expect(screen.getByLabelText("对话终态")).toBeTruthy();
    expect(screen.getByText("只靠正文")).toBeTruthy();
    expect(screen.getByText("使用 1 个工具")).toBeTruthy();
    expect(screen.queryByText("web_search")).toBeNull();
    expect(screen.queryByText("finish end_turn")).toBeNull();
    expect(screen.queryByLabelText("团队")).toBeNull();

    fireEvent.click(screen.getByText("使用 1 个工具"));
    expect(screen.getByText("web_search")).toBeTruthy();
  });

  it("keeps error and warning banners without outcome/finish chrome", () => {
    render(
      <ChatView
        content="x"
        projected={turn({
          turnWarning: "额度将尽",
          error: { code: "turn_failed", message: "回合失败" },
        })}
      />,
    );
    expect(screen.getByText("额度将尽")).toBeTruthy();
    expect(screen.getByText("回合失败")).toBeTruthy();
    expect(screen.queryByText("outcome ok")).toBeNull();
    expect(screen.queryByText("finish end_turn")).toBeNull();
    expect(screen.queryByText("助手")).toBeNull();
  });

  it("slots the team tree and a resolved ask into process markers, body before team", () => {
    const question = "先做官网还是先做品牌？";
    const body = "按品牌站流程做完了。";
    render(
      <ChatView
        content={body}
        projected={turn({
          process: [
            {
              kind: "tool",
              id: "tc1",
              tool_name: "write",
              arguments: {},
              result: null,
              status: "success",
            },
            { kind: "checkpoint", checkpoint_id: "cp1" },
            { kind: "team", execution_id: "ex1" },
          ],
          interactions: [
            {
              kind: "ask_user",
              id: "cp1",
              status: "resolved",
              question,
            },
          ],
        })}
      />,
    );

    expect(screen.queryByText("协作图")).toBeNull();
    expect(screen.getByLabelText("提问")).toBeTruthy();
    expect(screen.getByText(question)).toBeTruthy();
    expect(screen.getByText("调研员")).toBeTruthy();
    expect(screen.getByText(body)).toBeTruthy();
    expect(screen.queryByText("ask_user")).toBeNull();
    const ask = screen.getByLabelText("提问");
    const team = screen.getByLabelText("团队");
    const prose = screen.getByText(body);
    expect(ask.compareDocumentPosition(prose) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(prose.compareDocumentPosition(team) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("使用 1 个工具")).toBeTruthy();
    expect(screen.queryByText("write")).toBeNull();
  });

  it("keeps body before team tree when runs exist without a team marker", () => {
    const body = "正文先于协作树";
    render(
      <ChatView
        content={body}
        projected={turn({
          process: [
            {
              kind: "tool",
              id: "tc1",
              tool_name: "write",
              arguments: {},
              result: null,
              status: "success",
            },
          ],
        })}
      />,
    );
    const prose = screen.getByText(body);
    const team = screen.getByLabelText("团队");
    expect(prose.compareDocumentPosition(team) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("调研员")).toBeTruthy();
  });

  it("does not paint an empty 提问 label for a pending ask", () => {
    render(
      <ChatView
        content="正文"
        projected={turn({
          process: [{ kind: "checkpoint", checkpoint_id: "cp1" }],
          interactions: [
            {
              kind: "ask_user",
              id: "cp1",
              status: "pending",
              question: "还没答的问题",
            },
          ],
          runs: [],
        })}
      />,
    );
    expect(screen.queryByLabelText("提问")).toBeNull();
    expect(screen.queryByText("还没答的问题")).toBeNull();
    expect(screen.getByText("提问")).toBeTruthy();
    expect(screen.getByText("pending")).toBeTruthy();
  });

  it("does not paint 无正文 when tools/team already occupy the lane", () => {
    render(
      <ChatView
        content=""
        projected={turn({
          content: "",
          process: [
            { kind: "team", execution_id: "ex1" },
            {
              kind: "tool",
              id: "tc1",
              tool_name: "write",
              arguments: {},
              result: null,
              status: "success",
            },
          ],
        })}
      />,
    );
    expect(screen.getAllByLabelText("团队")).toHaveLength(1);
    expect(screen.getByText("使用 1 个工具")).toBeTruthy();
    expect(screen.queryByText("（无正文）")).toBeNull();
  });

  it("paints the team tree only once even with two team markers", () => {
    render(
      <ChatView
        content=""
        projected={turn({
          content: "",
          process: [
            { kind: "team", execution_id: "ex1" },
            { kind: "team", execution_id: "ex1" },
          ],
        })}
      />,
    );
    expect(screen.getAllByLabelText("团队")).toHaveLength(1);
    expect(screen.queryByText("（无正文）")).toBeNull();
  });
});
