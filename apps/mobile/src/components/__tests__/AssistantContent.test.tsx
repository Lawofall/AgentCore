// @vitest-environment jsdom
/**
 * Render tests for the mobile assistant message renderer (前端技术与架构 §七 富渲染, AUD-012).
 *
 * AssistantContent is the shared shape consumer for both live folds and history replay. These
 * pin its composition logic — which sub-view it picks per props — with the heavy leaf children
 * (Markdown / DebateView / TeamView) stubbed, so the test targets AssistantContent's own
 * branching (process-timeline vs team/reasoning/content, debate overlay, the inline tool step,
 * citations, and captainContext routed into the footer「更多」sheet — including system), not
 * those leaves. The block comment keeps the @vitest-environment directive file-leading.
 */

import {
  AssistantContent,
  graphAppendAnchorLabel,
} from "@/components/AssistantView";
import type {
  Citation,
  ContextBlockWire,
  DebateResultPayload,
  ProcessStep,
} from "@agentcore/contract-types";
import type { ProjectedRun } from "@agentcore/protocol-conformance";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/InteractionSheet", () => ({
  InteractionSheet: ({
    title,
    children,
    onCollapse,
    footer,
  }: {
    title: string;
    children: React.ReactNode;
    onCollapse: () => void;
    footer: React.ReactNode;
  }) => (
    <div data-testid="interaction-sheet" data-title={title}>
      {children}
      <div>{footer}</div>
      <button type="button" onClick={onCollapse}>
        close-mock
      </button>
    </div>
  ),
}));

vi.mock("@/components/Modal", () => ({
  Modal: ({
    children,
    label,
  }: {
    children: React.ReactNode;
    label?: string;
  }) => (
    <div data-testid="copy-mode-sheet" data-label={label}>
      {children}
    </div>
  ),
}));

vi.mock("@/components/Markdown", () => ({
  Markdown: ({
    content,
    evidenceLedger,
  }: {
    content: string;
    evidenceLedger?: { id: string }[];
  }) => (
    <div
      data-testid="md"
      data-ledger={evidenceLedger?.map((e) => e.id).join(",") ?? ""}
    >
      {content}
    </div>
  ),
}));
vi.mock("@/components/DebateView", () => ({
  DebateView: () => <div data-testid="debate" />,
  LiveDebateNarrative: () => <div data-testid="live-debate" />,
}));
vi.mock("@/components/TeamView", () => ({
  TeamView: () => <div data-testid="team" />,
}));

afterEach(cleanup);

function ctxBlock(
  p: Partial<ContextBlockWire> & { channel: ContextBlockWire["channel"] },
): ContextBlockWire {
  return {
    heading: "",
    body: "",
    chars: 0,
    truncated: false,
    source_role: "",
    source_run_id: "",
    fidelity: "",
    files: [],
    ...p,
  };
}

function makeRun(p: Partial<ProjectedRun> & { id: string }): ProjectedRun {
  return {
    agentId: "a1",
    task: "task",
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
    role: null,
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
    ...p,
  };
}

describe("graphAppendAnchorLabel", () => {
  it("统一为新开一队、接着上一张继续，不再报追加人数", () => {
    expect(graphAppendAnchorLabel(2, "debate")).toBe(
      "新开一队、接着上一张继续",
    );
    expect(graphAppendAnchorLabel(2, "debate", "auto")).toBe(
      "新开一队、接着上一张继续 · 自动开辩",
    );
    expect(graphAppendAnchorLabel(2)).toBe("新开一队、接着上一张继续");
    expect(graphAppendAnchorLabel(1, "multi_agent")).toBe(
      "新开一队、接着上一张继续",
    );
  });
});

describe("AssistantContent", () => {
  it("renders a pure-chat turn as Markdown, with no team/debate overlay", () => {
    render(<AssistantContent content="你好世界" />);
    expect(screen.getByTestId("md").textContent).toBe("你好世界");
    expect(screen.queryByTestId("team")).toBeNull();
    expect(screen.queryByTestId("debate")).toBeNull();
  });

  it("forwards turn evidenceLedger to Markdown (research #rN channel)", () => {
    render(
      <AssistantContent
        content="见 #r1"
        evidenceLedger={[
          {
            id: "#r1",
            url: "https://example.com",
            title: "源",
            site: "example.com",
          },
        ]}
      />,
    );
    expect(screen.getByTestId("md").getAttribute("data-ledger")).toBe("#r1");
  });

  it("does not fall back team debate ledger into Markdown turn channel", () => {
    render(
      <AssistantContent
        content="见 #r1"
        team={{
          agents: [],
          runs: [makeRun({ id: "run1" })],
          progress: { completed: 1, total: 1 },
          evidenceLedger: [
            {
              id: "#e1",
              url: "https://debate.example",
              title: "辩",
              site: "debate.example",
            },
          ],
        }}
      />,
    );
    expect(screen.getByTestId("md").getAttribute("data-ledger")).toBe("");
  });

  it("renders citations as a numbered 来源 list", () => {
    const citations: Citation[] = [
      { url: "https://a.com/post", title: "A 标题", site: "a.com" },
    ];
    render(<AssistantContent content="" citations={citations} />);
    expect(screen.getByText("来源 1")).toBeTruthy();
    const link = screen.getByRole("link", { name: /来源 1：A 标题/ });
    expect(link.getAttribute("href")).toBe("https://a.com/post");
    expect(screen.getByText("a.com")).toBeTruthy();
  });

  it("shows finishReason chip for degraded turns", () => {
    render(<AssistantContent content="降级后的短答" finishReason="degraded" />);
    expect(screen.getByTestId("finish-reason-chip").textContent).toBe(
      "空响应收尾",
    );
  });

  it("does not paint error as a top chip (hard failure = red card only)", () => {
    render(<AssistantContent content="半成品" finishReason="error" />);
    expect(screen.queryByTestId("finish-reason-chip")).toBeNull();
  });

  it("hides finishReason chip when failureNotice already owns the surface", () => {
    render(
      <AssistantContent
        content=""
        finishReason="degraded"
        finishDiagnosisLabel="模型返回空内容"
        failureNotice="模型多次空响应后收尾"
      />,
    );
    expect(screen.queryByTestId("finish-reason-chip")).toBeNull();
  });

  it("names the failed tool on unproductive-with-body", () => {
    const process: ProcessStep[] = [
      { kind: "content", text: "已写完大半" },
      {
        kind: "tool",
        id: "tc1",
        tool_name: "host_shell",
        arguments: { command: "do_work" },
        result: "host_shell failed",
        status: "error",
      },
    ];
    render(
      <AssistantContent
        content="已写完大半"
        process={process}
        finishReason="unproductive"
      />,
    );
    expect(
      screen.getByTestId("unproductive-tool-failure-hint").textContent,
    ).toBe("host_shell 未成功");
  });

  it("hides the failed-tool hint while streaming", () => {
    const process: ProcessStep[] = [
      {
        kind: "tool",
        id: "tc1",
        tool_name: "host_shell",
        arguments: {},
        result: "failed",
        status: "error",
      },
    ];
    render(
      <AssistantContent
        content="已写完大半"
        process={process}
        finishReason="unproductive"
        isStreaming
      />,
    );
    expect(screen.queryByTestId("unproductive-tool-failure-hint")).toBeNull();
  });

  it("renders citation tier badges when tier is present", () => {
    const citations: Citation[] = [
      {
        url: "https://www.bjnews.com.cn/detail/1.html",
        title: "新京报",
        site: "bjnews.com.cn",
        tier: "media",
      },
      {
        url: "https://example.com/x",
        title: "待评源",
        site: "example.com",
        tier: "unknown",
      },
    ];
    render(<AssistantContent content="" citations={citations} />);
    expect(screen.getByText("媒体")).toBeTruthy();
    expect(screen.getByText("待评")).toBeTruthy();
  });

  it("routes 收到的上下文 into footer 更多 (includes system; full length)", () => {
    render(
      <AssistantContent
        content="答案"
        captainContext={[
          ctxBlock({ channel: "system", body: "SECRET SYSTEM PROMPT" }),
          ctxBlock({
            channel: "request",
            heading: "登录页",
            body: "做个登录页",
          }),
        ]}
      />,
    );
    // No inline collapsible in the bubble body.
    expect(screen.queryByText("收到的上下文 · 2 段")).toBeNull();
    fireEvent.click(screen.getByTestId("assistant-footer-more"));
    expect(screen.getByTestId("received-context-menu-item").textContent).toBe(
      "收到的上下文 · 2 段",
    );
    fireEvent.click(screen.getByTestId("received-context-menu-item"));
    expect(screen.getByText("系统提示")).toBeTruthy();
    expect(screen.getByText("SECRET SYSTEM PROMPT")).toBeTruthy();
    expect(screen.getByText("原始请求")).toBeTruthy();
    expect(screen.getByText("做个登录页")).toBeTruthy();
  });

  it("hides 收到的上下文 menu entry when captainContext is empty", () => {
    render(
      <AssistantContent
        content="答案"
        captainContext={[]}
        usage={{
          input: 10,
          output: 5,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 10,
        }}
      />,
    );
    fireEvent.click(screen.getByTestId("assistant-footer-more"));
    expect(screen.queryByTestId("received-context-menu-item")).toBeNull();
    expect(screen.getByText("用量详情")).toBeTruthy();
  });

  it("overlays the debate product when present", () => {
    render(
      <AssistantContent content="ignored" debate={{} as DebateResultPayload} />,
    );
    expect(screen.getByTestId("debate")).toBeTruthy();
  });

  it("renders an inline tool step with its English label, detail and status", () => {
    const process: ProcessStep[] = [
      {
        kind: "tool",
        id: "t1",
        tool_name: "web_search",
        arguments: { query: "openai 新闻" },
        result: null,
        status: "success",
      },
    ];
    // isStreaming keeps process rows expanded (settled folds into「Used N tools」).
    render(<AssistantContent content="" process={process} isStreaming />);
    expect(screen.getByText("Search web")).toBeTruthy();
    expect(screen.getByText("openai 新闻")).toBeTruthy();
    expect(screen.getByText("完成")).toBeTruthy();
  });

  it("shows wait tool rows and wait-idle reasoning (no view-layer omit)", () => {
    const process: ProcessStep[] = [
      { kind: "reasoning", text: "空等队员" },
      {
        kind: "tool",
        id: "w1",
        tool_name: "wait",
        arguments: {
          reason: "工程实践研究员已完成，学术视角研究员仍在跑…",
        },
        result:
          "已确认等待团队事件（无需处置）。继续静默听团；勿再为等待而调用 delegate / update_synthesis。",
        status: "success",
      },
      { kind: "content", text: "旁白" },
    ];
    render(<AssistantContent content="" process={process} isStreaming />);
    expect(screen.getByText("Wait")).toBeTruthy();
    expect(screen.getByText("完成")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Wait/ })).toBeNull();
    expect(screen.queryByText(/研究员仍在跑/)).toBeNull();
    expect(screen.queryByText(/已确认等待团队事件/)).toBeNull();
    fireEvent.click(screen.getByText("Wait"));
    expect(screen.queryByText(/研究员仍在跑/)).toBeNull();
    expect(screen.queryByText(/已确认等待团队事件/)).toBeNull();
    expect(screen.getByText("Thought")).toBeTruthy();
    fireEvent.click(screen.getByText("Thought"));
    expect(screen.getByText("空等队员")).toBeTruthy();
    expect(screen.queryByText("Thinking…")).toBeNull();
  });

  // Tool execution phase (network search UX)
  it("shows the live phase text for a running web_search tool", () => {
    const process: ProcessStep[] = [
      {
        kind: "tool",
        id: "t1",
        tool_name: "web_search",
        arguments: { query: "天气" },
        result: null,
        status: "running",
      },
    ];
    render(
      <AssistantContent
        content=""
        process={process}
        isStreaming
        toolPhases={new Map([["t1", "querying"]])}
      />,
    );
    // Phase text replaces the bare「进行中」(a timer may be appended once ≥1s elapses).
    expect(screen.getByText("正在检索")).toBeTruthy();
    expect(screen.queryByText("进行中")).toBeNull();
    expect(screen.queryByText("Searching")).toBeNull();
  });

  it("falls back to 进行中 for a running tool with no known phase", () => {
    const process: ProcessStep[] = [
      {
        kind: "tool",
        id: "t1",
        tool_name: "web_search",
        arguments: { query: "天气" },
        result: null,
        status: "running",
      },
    ];
    render(<AssistantContent content="" process={process} isStreaming />);
    expect(screen.getByText("进行中")).toBeTruthy();
  });

  it("collapses settled Thought+tools into a process summary", () => {
    const process: ProcessStep[] = [
      { kind: "reasoning", text: "plan" },
      {
        kind: "tool",
        id: "t1",
        tool_name: "web_search",
        arguments: { query: "q" },
        result: null,
        status: "success",
      },
      { kind: "content", text: "answer" },
    ];
    render(<AssistantContent content="" process={process} />);
    expect(screen.getByText("Thought 1 step · Used 1 tool")).toBeTruthy();
    expect(screen.queryByText("Search web")).toBeNull();
    expect(screen.getByTestId("md").textContent).toBe("answer");
  });

  it("renders the team graph for a multi-agent turn", () => {
    render(
      <AssistantContent
        content=""
        process={[]}
        team={{
          agents: [],
          runs: [makeRun({ id: "r1" })],
          progress: { completed: 1, total: 1 },
        }}
      />,
    );
    expect(screen.getByTestId("team")).toBeTruthy();
  });

  it("hides TeamView when all workers are still pending (开工挂起零开跑)", () => {
    render(
      <AssistantContent
        content=""
        process={[]}
        team={{
          agents: [],
          runs: [
            makeRun({ id: "r1", status: "pending" }),
            makeRun({ id: "r2", status: "pending" }),
          ],
          progress: { completed: 0, total: 2 },
        }}
      />,
    );
    expect(screen.queryByTestId("team")).toBeNull();
  });

  it("hides TeamView when workers were skipped before start", () => {
    render(
      <AssistantContent
        content=""
        process={[]}
        team={{
          agents: [],
          runs: [
            makeRun({ id: "r1", status: "skipped" }),
            makeRun({ id: "r2", status: "skipped" }),
          ],
          progress: { completed: 0, total: 2 },
        }}
      />,
    );
    expect(screen.queryByTestId("team")).toBeNull();
  });

  it("still shows TeamView mid-wave when a completed run exists (plan_review pause)", () => {
    render(
      <AssistantContent
        content=""
        process={[]}
        team={{
          agents: [],
          runs: [
            makeRun({ id: "r1", status: "completed" }),
            makeRun({ id: "r2", status: "pending" }),
          ],
          progress: { completed: 1, total: 2 },
        }}
      />,
    );
    expect(screen.getByTestId("team")).toBeTruthy();
  });

  it("gates TeamView on process team marker when runs never started", () => {
    const process: ProcessStep[] = [{ kind: "team", execution_id: "exec-1" }];
    render(
      <AssistantContent
        content=""
        process={process}
        isStreaming
        team={{
          agents: [],
          runs: [makeRun({ id: "r1", status: "pending" })],
          progress: { completed: 0, total: 1 },
        }}
      />,
    );
    expect(screen.queryByTestId("team")).toBeNull();
  });

  it("renders interjection bubble at user_interjection marker slot", () => {
    const process: ProcessStep[] = [
      { kind: "content", text: "先说一句" },
      { kind: "user_interjection", interjection_id: "inj-1" },
      { kind: "content", text: "再回应" },
    ];
    const { container } = render(
      <AssistantContent
        content="先说一句再回应"
        process={process}
        turnClosed
        userInterjections={[
          {
            interjectionId: "inj-1",
            content: "改成中文",
            status: "injected",
          },
        ]}
      />,
    );
    const bubble = screen.getByTestId("interjection-bubble-inj-1");
    expect(bubble).toBeTruthy();
    expect(screen.getByText("主 Agent 已看到")).toBeTruthy();
    // marker 钉在两段正文之间，而非整块助手外挂。
    const mdNodes = screen.getAllByTestId("md");
    expect(mdNodes.map((n) => n.textContent)).toEqual(["先说一句", "再回应"]);
    const timeline = container.querySelector(".timeline");
    expect(timeline).toBeTruthy();
    const kids = [...(timeline?.children ?? [])];
    const bubbleIdx = kids.findIndex((el) =>
      el.querySelector?.("[data-testid='interjection-bubble-inj-1']"),
    );
    expect(bubbleIdx).toBeGreaterThan(0);
    expect(bubbleIdx).toBeLessThan(kids.length - 1);
  });

  it("falls back unmarked interjections when process has no marker", () => {
    render(
      <AssistantContent
        content="你好"
        process={[{ kind: "content", text: "你好" }]}
        turnClosed
        userInterjections={[
          {
            interjectionId: "inj-legacy",
            content: "旧 journal 插话",
            status: "injected",
          },
        ]}
      />,
    );
    expect(screen.getByTestId("interjection-bubble-inj-legacy")).toBeTruthy();
  });
});
