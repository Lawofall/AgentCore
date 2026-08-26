// @vitest-environment jsdom
/**
 * Conversation replay main column (user perspective):
 * - user/assistant bubbles via UserBubble + ChatView
 * - execution_harvest stays out of this lane
 * - worker prose stays out of the timeline (in dock)
 * - team node click → onSelectRun (dock opens via parent)
 */
import { ChatTimeline } from "@/components/conversation-replay/ChatTimeline";
import { InspectorPanel } from "@/components/conversation-replay/InspectorPanel";
import { foldEmptyAssistantFollowers } from "@/lib/foldEmptyAssistant";
import type {
  ReplayMessage,
  ReplayRun,
  ReplaySpan,
} from "@/services/adminObservability";
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function span(p: Partial<ReplaySpan> & { kind: string }): ReplaySpan {
  return {
    args_preview: null,
    finish_reason: null,
    input_tokens: null,
    name: null,
    output_tokens: null,
    result_preview: null,
    round_idx: null,
    run_id: null,
    success: true,
    ...p,
  };
}

function run(p: Partial<ReplayRun> & { run_id: string }): ReplayRun {
  return {
    agent_id: p.agent_id ?? p.run_id,
    content: null,
    debrief: null,
    depends_on: [],
    error: null,
    kind: "agent",
    output_summary: null,
    parent_run_id: null,
    role: null,
    status: "completed",
    task: "",
    ...p,
  };
}

function msg(p: Partial<ReplayMessage> & { id: string; role: string }): ReplayMessage {
  return {
    content: null,
    cost_total: 0,
    created_at: "2026-08-01T00:00:00Z",
    credential_source: null,
    harvest_kind: null,
    metrics: null,
    models: [],
    origin: null,
    runs: [],
    runs_payload: null,
    projected: null,
    has_final_state: false,
    spans: [],
    trace_id: null,
    ...p,
  };
}

describe("ChatTimeline chat layout", () => {
  it("renders user bubble and assistant ChatView body", () => {
    const messages: ReplayMessage[] = [
      msg({ id: "u1", role: "user", content: "帮我查一下" }),
      msg({
        id: "a1",
        role: "assistant",
        content: "查完了，结论如下。",
        spans: [
          span({
            kind: "tool",
            name: "web_search",
            args_preview: "q=foo",
            result_preview: "3 hits",
            success: true,
          }),
          span({
            kind: "llm",
            round_idx: 0,
            finish_reason: "stop",
            input_tokens: 10,
            output_tokens: 20,
          }),
        ],
      }),
    ];

    render(
      <ChatTimeline
        messages={messages}
        selectedId="a1"
        selectedRunId={null}
        onSelect={vi.fn()}
        onSelectRun={vi.fn()}
        isAnchored={() => false}
      />,
    );

    expect(screen.getByText("帮我查一下")).toBeTruthy();
    expect(screen.getByText("查完了，结论如下。")).toBeTruthy();
    expect(screen.queryByText("用户")).toBeNull();
    expect(screen.queryByText("助手")).toBeNull();
    expect(screen.getByLabelText("对话终态")).toBeTruthy();
    // Span ops left the main column — not folded into ChatView, not reverse-engineered.
    expect(screen.queryByText("1 次模型调用 · 1 次工具")).toBeNull();
    expect(screen.queryByText("web_search")).toBeNull();
  });

  it("assistant footer shows cost · extra rounds · duration, not span counts", () => {
    render(
      <ChatTimeline
        messages={[
          msg({
            id: "a1",
            role: "assistant",
            content: "结论",
            cost_total: 100_000_000,
            metrics: {
              rounds: 3,
              duration_ms: 1500,
            } as ReplayMessage["metrics"],
            spans: [
              span({ kind: "llm", round_idx: 0 }),
              span({ kind: "tool", name: "web_search" }),
            ],
          }),
        ]}
        selectedId={null}
        selectedRunId={null}
        onSelect={vi.fn()}
        onSelectRun={vi.fn()}
        isAnchored={() => false}
      />,
    );

    const footer = screen.getByLabelText("回合用量");
    expect(footer.textContent).toMatch(/¥0\.10/);
    expect(footer.textContent).toMatch(/3 轮/);
    expect(footer.textContent).toMatch(/1\.5s/);
    expect(screen.queryByText("1 次模型调用 · 1 次工具")).toBeNull();
  });

  it("paints user attachment and @Agent chips without download links", () => {
    const messages: ReplayMessage[] = [
      msg({
        id: "u1",
        role: "user",
        content: "看这份",
        attachments: [
          {
            binary: false,
            kind: "file",
            name: "brief.pdf",
            path: "attachments/brief.pdf",
            size_bytes: 2048,
            truncated: false,
          },
        ],
        agent_mentions: [{ agent_id: "researcher", role: "调研员" }],
      }),
    ];

    render(
      <ChatTimeline
        messages={messages}
        selectedId={null}
        selectedRunId={null}
        onSelect={vi.fn()}
        onSelectRun={vi.fn()}
        isAnchored={() => false}
      />,
    );

    expect(screen.getByLabelText("@Agent").textContent).toContain("@调研员");
    expect(screen.getByLabelText("附件").textContent).toContain("brief.pdf");
    expect(screen.getByText("文件")).toBeTruthy();
    expect(
      screen.queryByRole("link", { name: /brief\.pdf/ }),
    ).toBeNull();
  });

  it("omits execution_harvest from the user-facing timeline", () => {
    const messages: ReplayMessage[] = [
      msg({
        id: "h1",
        role: "user",
        origin: "execution_harvest",
        harvest_kind: "cancelled",
        content:
          "【系统收口】后台团队任务已取消或中断。请基于已完成部分向老板简要收尾。",
      }),
      msg({
        id: "a1",
        role: "assistant",
        content: "按已完成部分收尾。",
      }),
    ];

    render(
      <ChatTimeline
        messages={messages}
        selectedId={null}
        selectedRunId={null}
        onSelect={vi.fn()}
        onSelectRun={vi.fn()}
        isAnchored={() => false}
      />,
    );

    expect(screen.queryByText("系统收口")).toBeNull();
    expect(screen.queryByText("已取消")).toBeNull();
    expect(screen.queryByText("用户")).toBeNull();
    expect(
      screen.queryByText(
        "【系统收口】后台团队任务已取消或中断。请基于已完成部分向老板简要收尾。",
      ),
    ).toBeNull();
    expect(screen.getByText("按已完成部分收尾。")).toBeTruthy();
  });

  it("omits prefix-only harvest rows from the timeline too", () => {
    const messages: ReplayMessage[] = [
      msg({
        id: "h1",
        role: "user",
        content: "【系统收口】后台团队任务已全部完成。请综合队员产出。",
      }),
    ];

    render(
      <ChatTimeline
        messages={messages}
        selectedId={null}
        selectedRunId={null}
        onSelect={vi.fn()}
        onSelectRun={vi.fn()}
        isAnchored={() => false}
      />,
    );

    expect(screen.queryByText("系统收口")).toBeNull();
    expect(screen.queryByText("已完成")).toBeNull();
    expect(screen.queryByText("用户")).toBeNull();
    expect(screen.getByText("该会话暂无用户可见消息")).toBeTruthy();
  });

  it("does not dump worker body into the timeline; graph click selects run", () => {
    const onSelectRun = vi.fn();
    const workerBody = "队员私有长文不应出现在主栏";
    const messages: ReplayMessage[] = [
      msg({
        id: "a1",
        role: "assistant",
        content: "CEO 汇总",
        runs: [
          run({
            run_id: "r-worker",
            role: "研究员",
            task: "搜集资料",
            content: workerBody,
            status: "completed",
          }),
        ],
        projected: {
          runs: [
            {
              id: "r-worker",
              role: "研究员",
              status: "completed",
              task: "搜集资料",
            },
          ],
          progress: { completed: 1, total: 1 },
        },
      }),
    ];

    render(
      <ChatTimeline
        messages={messages}
        selectedId="a1"
        selectedRunId={null}
        onSelect={vi.fn()}
        onSelectRun={onSelectRun}
        isAnchored={() => false}
      />,
    );

    expect(screen.getByText("CEO 汇总")).toBeTruthy();
    expect(screen.getByLabelText("团队")).toBeTruthy();
    expect(screen.queryByText(workerBody)).toBeNull();

    fireEvent.click(screen.getByText("研究员"));
    expect(onSelectRun).toHaveBeenCalledWith("r-worker");
  });

  it("after folding an empty trailing assistant, process and team paint once", () => {
    const projected = {
      process: [
        { kind: "team", execution_id: "ex1" },
        {
          kind: "tool",
          id: "t1",
          tool_name: "read",
          status: "success",
        },
      ],
      runs: [
        {
          id: "captain",
          role: "captain",
          status: "completed",
          task: "统筹",
        },
      ],
      progress: { completed: 1, total: 1 },
    };
    const { messages } = foldEmptyAssistantFollowers([
      msg({ id: "u1", role: "user", content: "审计" }),
      msg({
        id: "a1",
        role: "assistant",
        content: "结论如下",
        projected,
        has_final_state: true,
      }),
      msg({
        id: "a2",
        role: "assistant",
        content: "",
        projected,
        has_final_state: true,
      }),
    ]);

    render(
      <ChatTimeline
        messages={messages}
        selectedId="a1"
        selectedRunId={null}
        onSelect={vi.fn()}
        onSelectRun={vi.fn()}
        isAnchored={() => false}
      />,
    );

    expect(screen.getAllByLabelText("团队")).toHaveLength(1);
    expect(screen.getAllByText("使用 1 个工具")).toHaveLength(1);
    expect(screen.queryByText("（无正文）")).toBeNull();
    expect(screen.getByText("结论如下")).toBeTruthy();
  });

  it("气泡不再吞掉内部控件的键盘事件（协作图节点可键盘点选）", () => {
    const onSelect = vi.fn();
    const onSelectRun = vi.fn();
    const messages: ReplayMessage[] = [
      msg({
        id: "a1",
        role: "assistant",
        content: "结论",
        projected: {
          runs: [
            {
              id: "r-worker",
              role: "研究员",
              status: "completed",
            },
          ],
        },
      }),
    ];

    render(
      <ChatTimeline
        messages={messages}
        selectedId="a1"
        selectedRunId={null}
        onSelect={onSelect}
        onSelectRun={onSelectRun}
        isAnchored={() => false}
      />,
    );

    const node = screen.getByText("研究员").closest("button");
    expect(node).toBeTruthy();
    fireEvent.keyDown(node as HTMLElement, { key: "Enter" });
    fireEvent.keyDown(node as HTMLElement, { key: " " });
    expect(onSelect).not.toHaveBeenCalled();

    const bubble = (node as HTMLElement).parentElement?.closest(
      '[role="button"]',
    );
    expect(bubble).toBeTruthy();
    fireEvent.keyDown(bubble as HTMLElement, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("shows a truncation banner when earlier messages were cut", () => {
    render(
      <ChatTimeline
        messages={[msg({ id: "a1", role: "assistant", content: "最近一条" })]}
        selectedId={null}
        selectedRunId={null}
        onSelect={vi.fn()}
        onSelectRun={vi.fn()}
        isAnchored={() => false}
        hasMoreBefore
      />,
    );

    expect(
      screen.getByRole("status").textContent,
    ).toContain("更早的消息已被截断");
  });

  it("shows a loading status while a turn's final state hydrates", () => {
    render(
      <ChatTimeline
        messages={[
          msg({
            id: "a1",
            role: "assistant",
            content: "最近一条",
            has_final_state: true,
          }),
        ]}
        selectedId="a1"
        selectedRunId={null}
        onSelect={vi.fn()}
        onSelectRun={vi.fn()}
        isAnchored={() => false}
        hydratingIds={["a1"]}
      />,
    );

    expect(screen.getByRole("status").textContent).toContain("正在加载终态");
    expect(screen.getByText("最近一条")).toBeTruthy();
  });

  it("does not show 正在加载终态 when the row has no final state to fetch", () => {
    render(
      <ChatTimeline
        messages={[msg({ id: "a1", role: "assistant", content: "最近一条" })]}
        selectedId="a1"
        selectedRunId={null}
        onSelect={vi.fn()}
        onSelectRun={vi.fn()}
        isAnchored={() => false}
        hydratingIds={["a1"]}
      />,
    );

    expect(screen.queryByText("正在加载终态")).toBeNull();
  });

  it("hides 正在加载终态 once runs_payload is already on the row", () => {
    render(
      <ChatTimeline
        messages={[
          msg({
            id: "a1",
            role: "assistant",
            content: "最近一条",
            has_final_state: true,
            runs_payload: { events_complete: true, process: [] },
          }),
        ]}
        selectedId="a1"
        selectedRunId={null}
        onSelect={vi.fn()}
        onSelectRun={vi.fn()}
        isAnchored={() => false}
        hydratingIds={["a1"]}
      />,
    );

    expect(screen.queryByText("正在加载终态")).toBeNull();
  });

  it("offers a retry when hydrating final state failed", () => {
    const onRetry = vi.fn();
    render(
      <ChatTimeline
        messages={[msg({ id: "a1", role: "assistant", content: "最近一条" })]}
        selectedId="a1"
        selectedRunId={null}
        onSelect={vi.fn()}
        onSelectRun={vi.fn()}
        isAnchored={() => false}
        hydrateError="发生未知错误"
        onRetryHydrate={onRetry}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "重试加载终态" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("InspectorPanel worker dock", () => {
  it("shows worker content and can close the worker tab", () => {
    const onCloseWorkerTab = vi.fn();
    const message = msg({
      id: "a1",
      role: "assistant",
      content: "CEO",
      runs: [
        run({
          run_id: "r1",
          role: "写手",
          task: "起草",
          content: "队员正文在此",
        }),
      ],
      spans: [
        span({
          kind: "tool",
          name: "file_read",
          run_id: "r1",
          result_preview: "ok",
        }),
      ],
    });

    render(
      <InspectorPanel
        message={message}
        activeTab="r1"
        workerTabIds={["r1"]}
        onActivateTab={vi.fn()}
        onCloseWorkerTab={onCloseWorkerTab}
        onSelectRun={vi.fn()}
        onClose={vi.fn()}
        cnyLabel="¥0.01"
      />,
    );

    expect(screen.getByRole("tab", { name: /诊断/ })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "写手" })).toBeTruthy();
    expect(screen.getByText("队员正文在此")).toBeTruthy();
    expect(screen.getByText("起草")).toBeTruthy();
    expect(screen.getByText("file_read")).toBeTruthy();
    expect(screen.queryByText(/过程明细/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "关闭 写手" }));
    expect(onCloseWorkerTab).toHaveBeenCalledWith("r1");
    expect(screen.queryByText("返回列表")).toBeNull();
  });

  it("renders the worker process timeline in the dock instead of only the final body", () => {
    const message = msg({
      id: "a1",
      role: "assistant",
      content: "CEO",
      runs: [
        run({
          run_id: "r1",
          role: "写手",
          task: "起草",
          content: "队员正文在此",
        }),
      ],
      projected: {
        runs: [
          {
            id: "r1",
            role: "写手",
            process: [
              { kind: "reasoning", text: "先列提纲。" },
              {
                kind: "tool",
                id: "t1",
                tool_name: "web_search",
                status: "success",
              },
            ],
          },
        ],
      },
    });

    render(
      <InspectorPanel
        message={message}
        activeTab="r1"
        workerTabIds={["r1"]}
        onActivateTab={vi.fn()}
        onCloseWorkerTab={vi.fn()}
        onSelectRun={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("web_search")).toBeTruthy();
    expect(screen.getByText("队员正文在此")).toBeTruthy();
    expect(screen.queryByText("思考 1 步 · 使用 1 个工具")).toBeNull();
    expect(screen.queryByText(/^过程$/)).toBeNull();
    expect(screen.queryByText(/^产出$/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^思考$/ }));
    expect(screen.getByText("先列提纲。")).toBeTruthy();
  });

  it("shows diagnosis tab and worker short list when on diagnosis", () => {
    const message = msg({
      id: "a1",
      role: "assistant",
      content: "CEO",
      runs: [run({ run_id: "r1", role: "写手" })],
    });

    render(
      <InspectorPanel
        message={message}
        activeTab="diagnosis"
        workerTabIds={[]}
        onActivateTab={vi.fn()}
        onCloseWorkerTab={vi.fn()}
        onSelectRun={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole("tab", { selected: true, name: /诊断/ })).toBeTruthy();
    expect(screen.queryByText("运维")).toBeNull();
    expect(screen.queryByText("执行")).toBeNull();
    expect(screen.queryByText("检视")).toBeNull();
    expect(screen.getByText("写手")).toBeTruthy();
    expect(screen.queryByText("返回列表")).toBeNull();
  });

  it("keeps harvest attribution in the dock, not as a user bubble", () => {
    const harvest = msg({
      id: "h1",
      role: "user",
      origin: "execution_harvest",
      harvest_kind: "cancelled",
      content:
        "【系统收口】后台团队任务已取消或中断。请基于已完成部分向老板简要收尾。",
    });

    render(
      <InspectorPanel
        message={harvest}
        activeTab="diagnosis"
        workerTabIds={[]}
        onActivateTab={vi.fn()}
        onCloseWorkerTab={vi.fn()}
        onSelectRun={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getAllByText("系统收口").length).toBeGreaterThan(0);
    expect(screen.getByText("已取消")).toBeTruthy();
    expect(
      screen.getByText(
        "【系统收口】后台团队任务已取消或中断。请基于已完成部分向老板简要收尾。",
      ),
    ).toBeTruthy();
  });
});
