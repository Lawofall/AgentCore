// @vitest-environment jsdom
import { InterjectionTimeline } from "@/components/chat/InterjectionTimeline";
import { TooltipProvider } from "@/components/ui/tooltip";
import { inlineToken } from "@/lib/inlineBody";
import { DRAFT_KEY, useConversationStore } from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

afterEach(() => {
  cleanup();
});

beforeEach(() => {
  useExecutionStore.setState({ byId: {} });
  useConversationStore.setState({
    currentConversationId: null,
    byId: {
      [DRAFT_KEY]: {
        messages: [],
        memoryUpdates: [],
        isGenerating: false,
        turnPhase: "idle",
        abort: null,
        error: null,
        retry: null,
        errorAction: null,
        messageFocus: null,
        hasMoreBefore: false,
        hasMoreAfter: false,
        loadingOlder: false,
        loadingNewer: false,
        pendingTurnWarning: null,
        pendingTraceId: null,
        toolStartedMs: {},
        executionVia: null,
        waitingForWorkspaceLock: false,
        waitingForDeskProvision: false,
      },
    },
  });
});

function seedStreamingAssistant() {
  useConversationStore.setState((s) => ({
    byId: {
      ...s.byId,
      [DRAFT_KEY]: {
        ...s.byId[DRAFT_KEY],
        turnPhase: "streaming",
        messages: [
          {
            id: "m1",
            role: "assistant",
            content: "",
            createdAt: new Date().toISOString(),
            executionId: null,
            isStreaming: true,
          },
        ],
      },
    },
  }));
}

describe("InterjectionTimeline", () => {
  it("renders waiting copy while live turn is streaming", () => {
    seedStreamingAssistant();
    useExecutionStore.setState({
      byId: {
        m1: {
          userInterjections: [
            {
              interjectionId: "ij1",
              executionId: "e1",
              content: "补充成本对比",
              status: "received",
              note: null,
            },
            {
              interjectionId: "ij2",
              executionId: "e1",
              content: "无关贺卡",
              status: "queued",
              note: null,
            },
          ],
        },
      },
    } as never);

    render(
      <>
        <InterjectionTimeline messageId="m1" interjectionId="ij1" />
        <InterjectionTimeline messageId="m1" interjectionId="ij2" />
      </>,
    );
    expect(screen.getByTestId("interjection-bubble-ij1")).toBeTruthy();
    // queued 等待期仍是完整气泡，尚未折叠成锚点
    expect(screen.getByTestId("interjection-bubble-ij2")).toBeTruthy();
    expect(screen.queryByTestId("interjection-note-ij2")).toBeNull();
    expect(screen.getByText("补充成本对比")).toBeTruthy();
    expect(screen.getByText("已送达，等待主 Agent 读取")).toBeTruthy();
    expect(screen.getByText("将在下一条回复处理")).toBeTruthy();
    expect(screen.queryByText("已传达给团队")).toBeNull();
  });

  it("keeps full user bubble while queued with no later matching user message", () => {
    seedStreamingAssistant();
    useExecutionStore.setState({
      byId: {
        m1: {
          userInterjections: [
            {
              interjectionId: "ij-q",
              executionId: "e1",
              content: "排队正文应完整气泡展示",
              status: "queued",
              note: "可选备注",
            },
          ],
        },
      },
    } as never);

    render(<InterjectionTimeline messageId="m1" interjectionId="ij-q" />);
    expect(screen.getByTestId("interjection-bubble-ij-q")).toBeTruthy();
    expect(screen.queryByTestId("interjection-note-ij-q")).toBeNull();
    expect(screen.getByText("将在下一条回复处理")).toBeTruthy();
    expect(screen.getByText("排队正文应完整气泡展示")).toBeTruthy();
    const note = screen.getByTestId("interjection-server-note");
    expect(note.textContent).toBe("可选备注");
    expect(note.className).toContain("border-t");
    expect(note.className).toContain("text-muted-foreground/70");
  });

  it("folds queued into a one-line anchor that drops the duplicated body", () => {
    useConversationStore.setState((s) => ({
      byId: {
        ...s.byId,
        [DRAFT_KEY]: {
          ...s.byId[DRAFT_KEY],
          turnPhase: "idle",
          messages: [
            {
              id: "m1",
              role: "assistant",
              content: "上一回合答",
              createdAt: new Date().toISOString(),
              executionId: null,
              isStreaming: false,
            },
            {
              id: "u-dequeued",
              role: "user",
              content: "让他停止",
              createdAt: new Date().toISOString(),
              executionId: null,
              isStreaming: false,
            },
          ],
        },
      },
    }));
    useExecutionStore.setState({
      byId: {
        m1: {
          userInterjections: [
            {
              interjectionId: "ij-folded",
              executionId: "e1",
              content: "让他停止",
              status: "queued",
              note: "协调已收口，已自动转入下一回合",
            },
          ],
        },
      },
    } as never);

    render(<InterjectionTimeline messageId="m1" interjectionId="ij-folded" />);
    expect(screen.getByTestId("interjection-note-ij-folded")).toBeTruthy();
    expect(screen.queryByTestId("interjection-bubble-ij-folded")).toBeNull();
    // 出队后未来时已过期，且正文不再重复（下方正式气泡承载）
    expect(screen.getByText("已转入下一回合")).toBeTruthy();
    expect(screen.queryByText("将在下一条回复处理")).toBeNull();
    expect(screen.queryByText("让他停止")).toBeNull();
    expect(screen.getByTestId("interjection-server-note").textContent).toBe(
      "协调已收口，已自动转入下一回合",
    );
  });

  it("folds received when a later user bubble already carries the body", () => {
    useConversationStore.setState((s) => ({
      byId: {
        ...s.byId,
        [DRAFT_KEY]: {
          ...s.byId[DRAFT_KEY],
          turnPhase: "streaming",
          messages: [
            {
              id: "m1",
              role: "assistant",
              content: "正在说",
              createdAt: new Date().toISOString(),
              executionId: null,
              isStreaming: true,
            },
            {
              id: "u-steer",
              role: "user",
              content: "补充成本对比",
              createdAt: new Date().toISOString(),
              executionId: null,
              isStreaming: false,
            },
          ],
        },
      },
    }));
    useExecutionStore.setState({
      byId: {
        m1: {
          userInterjections: [
            {
              interjectionId: "ij-recv",
              executionId: "e1",
              content: "补充成本对比",
              status: "received",
              note: null,
            },
          ],
        },
      },
    } as never);

    render(<InterjectionTimeline messageId="m1" interjectionId="ij-recv" />);
    expect(screen.getByTestId("interjection-note-ij-recv")).toBeTruthy();
    expect(screen.queryByTestId("interjection-bubble-ij-recv")).toBeNull();
    expect(screen.getByText("已送达，等待主 Agent 读取")).toBeTruthy();
    expect(screen.queryByText("补充成本对比")).toBeNull();
  });

  it("does not fold queued when later user content differs", () => {
    useConversationStore.setState((s) => ({
      byId: {
        ...s.byId,
        [DRAFT_KEY]: {
          ...s.byId[DRAFT_KEY],
          turnPhase: "idle",
          messages: [
            {
              id: "m1",
              role: "assistant",
              content: "答",
              createdAt: new Date().toISOString(),
              executionId: null,
              isStreaming: false,
            },
            {
              id: "u-other",
              role: "user",
              content: "别的话",
              createdAt: new Date().toISOString(),
              executionId: null,
              isStreaming: false,
            },
          ],
        },
      },
    }));
    useExecutionStore.setState({
      byId: {
        m1: {
          userInterjections: [
            {
              interjectionId: "ij-keep",
              executionId: "e1",
              content: "排队原话",
              status: "queued",
              note: null,
            },
          ],
        },
      },
    } as never);

    render(<InterjectionTimeline messageId="m1" interjectionId="ij-keep" />);
    expect(screen.getByTestId("interjection-bubble-ij-keep")).toBeTruthy();
    expect(screen.queryByTestId("interjection-note-ij-keep")).toBeNull();
  });

  it("keeps non-queued statuses as user bubbles", () => {
    seedStreamingAssistant();
    const statuses = ["received", "injected", "addressed", "failed"] as const;
    useExecutionStore.setState({
      byId: {
        m1: {
          userInterjections: statuses.map((status) => ({
            interjectionId: `ij-${status}`,
            executionId: "e1",
            content: `正文-${status}`,
            status,
            note: null,
          })),
        },
      },
    } as never);

    render(
      statuses.map((status) => (
        <InterjectionTimeline
          key={status}
          messageId="m1"
          interjectionId={`ij-${status}`}
        />
      )),
    );
    for (const status of statuses) {
      expect(
        screen.getByTestId(`interjection-bubble-ij-${status}`),
      ).toBeTruthy();
      expect(screen.queryByTestId(`interjection-note-ij-${status}`)).toBeNull();
      expect(screen.getByText(`正文-${status}`)).toBeTruthy();
    }
  });

  it("hides addressed badge and in-graph note, keeps the user bubble", () => {
    seedStreamingAssistant();
    useExecutionStore.setState({
      byId: {
        m1: {
          userInterjections: [
            {
              interjectionId: "ij-addr",
              executionId: "e1",
              content: "停止",
              status: "addressed",
              note: "已在本回合停掉对应成员",
            },
          ],
        },
      },
    } as never);

    render(<InterjectionTimeline messageId="m1" interjectionId="ij-addr" />);
    expect(screen.getByTestId("interjection-bubble-ij-addr")).toBeTruthy();
    expect(screen.getByText("停止")).toBeTruthy();
    expect(screen.queryByTestId("interjection-status-ij-addr")).toBeNull();
    expect(screen.queryByTestId("interjection-server-note")).toBeNull();
    expect(screen.queryByText("已纳入本回合合成")).toBeNull();
    expect(screen.queryByText("已在本回合停掉对应成员")).toBeNull();
  });

  it("renders @ role chips matching history user bubbles", () => {
    seedStreamingAssistant();
    useExecutionStore.setState({
      byId: {
        m1: {
          userInterjections: [
            {
              interjectionId: "ij-mention",
              executionId: "e1",
              content: "请让研究员再核一遍成本。",
              status: "received",
              note: null,
              agentMentions: [{ agentId: "agent_research", role: "研究员" }],
            },
          ],
        },
      },
    } as never);

    render(
      <TooltipProvider>
        <InterjectionTimeline messageId="m1" interjectionId="ij-mention" />
      </TooltipProvider>,
    );
    expect(screen.getByTestId("interjection-bubble-ij-mention")).toBeTruthy();
    expect(screen.getByText("研究员")).toBeTruthy();
    expect(screen.getByText("点名")).toBeTruthy();
    expect(screen.getByText("请让研究员再核一遍成本。")).toBeTruthy();
    expect(screen.getByTestId("user-chip-tray")).toBeTruthy();
    expect(screen.queryByTestId("user-inline-body")).toBeNull();
  });

  it("renders marked @ chips inline and skips the tray", () => {
    seedStreamingAssistant();
    const content = `请让${inlineToken("M", 0)}再核一遍成本。`;
    useExecutionStore.setState({
      byId: {
        m1: {
          userInterjections: [
            {
              interjectionId: "ij-inline",
              executionId: "e1",
              content,
              status: "received",
              note: null,
              agentMentions: [{ agentId: "agent_research", role: "研究员" }],
            },
          ],
        },
      },
    } as never);

    render(
      <TooltipProvider>
        <InterjectionTimeline messageId="m1" interjectionId="ij-inline" />
      </TooltipProvider>,
    );
    expect(screen.getByTestId("interjection-bubble-ij-inline")).toBeTruthy();
    expect(screen.getByTestId("user-inline-body")).toBeTruthy();
    expect(screen.queryByTestId("user-chip-tray")).toBeNull();
    expect(screen.getAllByText("研究员")).toHaveLength(1);
    expect(screen.getByTestId("user-inline-body").textContent).toContain(
      "请让",
    );
    expect(screen.getByTestId("user-inline-body").textContent).not.toContain(
      "\uFFFC",
    );
  });

  it("renders unread copy when turn closed and status stays received", () => {
    useConversationStore.setState((s) => ({
      byId: {
        ...s.byId,
        [DRAFT_KEY]: {
          ...s.byId[DRAFT_KEY],
          turnPhase: "stopped",
          messages: [
            {
              id: "m1",
              role: "assistant",
              content: "",
              createdAt: new Date().toISOString(),
              executionId: null,
              isStreaming: false,
            },
          ],
        },
      },
    }));
    useExecutionStore.setState({
      byId: {
        m1: {
          userInterjections: [
            {
              interjectionId: "ij1",
              executionId: "e1",
              content: "停后未读",
              status: "received",
              note: null,
            },
          ],
        },
      },
    } as never);

    render(<InterjectionTimeline messageId="m1" interjectionId="ij1" />);
    expect(screen.getByText("未被主 Agent 读取")).toBeTruthy();
    expect(screen.queryByText("已送达，等待主 Agent 读取")).toBeNull();
  });

  it("renders nothing when empty", () => {
    const { container } = render(
      <InterjectionTimeline messageId="missing" interjectionId="ij-x" />,
    );
    expect(container.firstChild).toBeNull();
  });
});
