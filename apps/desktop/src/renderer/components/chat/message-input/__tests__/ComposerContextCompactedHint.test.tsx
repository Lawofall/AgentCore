// @vitest-environment jsdom
import { formatLocalMoment } from "@/lib/recoveryMoment";
import type { Conversation } from "@/stores/conversation";
import { useConversationStore } from "@/stores/conversation";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ComposerContextCompactedHint } from "../ComposerContextCompactedHint";

const useConversationsMock = vi.hoisted(() =>
  vi.fn(() => [] as Conversation[]),
);
vi.mock("@/hooks/useConversations", () => ({
  useConversations: useConversationsMock,
}));

const CID = "c1";

function conv(over: Partial<Conversation> = {}): Conversation {
  return {
    id: CID,
    title: "长对话",
    updatedAt: "2026-08-14T00:00:00Z",
    messageCount: 120,
    lastMessagePreview: null,
    ...over,
  };
}

function open(list: Conversation[], id: string | null = CID): void {
  useConversationsMock.mockReturnValue(list);
  useConversationStore.setState({ currentConversationId: id, byId: {} });
}

beforeEach(() => {
  open([], null);
});

afterEach(() => {
  cleanup();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
});

describe("ComposerContextCompactedHint", () => {
  it("renders nothing without a gap — success compaction is not composer chrome", () => {
    open([
      conv({
        contextCompacted: true,
        compactedThrough: "2026-08-01T00:00:00Z",
      }),
    ]);
    const { container } = render(<ComposerContextCompactedHint />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("composer-context-compacted-hint")).toBeNull();
  });

  it("压缩没跟上 → 说清丢了多少、原文还在、以及能怎么办", () => {
    open([conv({ contextGap: { droppedMessages: 32 } })]);
    render(<ComposerContextCompactedHint />);

    const text = screen.getByTestId("composer-context-gap-hint").textContent;
    expect(text).toContain("没能收入摘要"); // 什么没做成
    expect(text).toContain("32 条"); // 代价有多大
    expect(text).toContain("原文仍在"); // 没丢的东西
    expect(text).toContain("自动重试"); // 不是永久丧失
    expect(text).toContain("再说一遍"); // 用户能怎么办
    expect(screen.getByTestId("composer-context-gap-hint").className).toContain(
      "text-muted-foreground",
    );
  });

  it("从没压缩成功过也要说 —— 那正是线上整天失败的形状", () => {
    open([
      conv({ contextCompacted: false, contextGap: { droppedMessages: 5 } }),
    ]);
    render(<ComposerContextCompactedHint />);
    expect(screen.getByTestId("composer-context-gap-hint")).toBeTruthy();
  });

  it("上游给了日期就按本机时区报出恢复时刻，没给就只说会自动重试", () => {
    open([
      conv({
        contextGap: {
          droppedMessages: 8,
          recoveryAt: "2026-08-14T16:00:00Z",
        },
      }),
    ]);
    const { unmount } = render(<ComposerContextCompactedHint />);
    const dated = screen.getByTestId("composer-context-gap-hint").textContent;
    expect(dated).toContain(formatLocalMoment("2026-08-14T16:00:00Z"));
    expect(dated).toContain("恢复，届时自动补上");
    expect(dated).not.toContain("UTC");
    unmount();

    open([conv({ contextGap: { droppedMessages: 8, recoveryAt: null } })]);
    render(<ComposerContextCompactedHint />);
    const text = screen.getByTestId("composer-context-gap-hint").textContent;
    expect(text).toContain("自动重试");
    expect(text).not.toContain("恢复，届时");
  });

  it("短会话压缩失败不打扰：没有 gap 就不显示", () => {
    open([conv({ contextCompacted: true, messageCount: 12 })]);
    const { container } = render(<ComposerContextCompactedHint />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("composer-context-gap-hint")).toBeNull();
  });

  it("别的会话丢了历史不算这一个的账", () => {
    open([conv({ id: "other", contextGap: { droppedMessages: 99 } })]);
    const { container } = render(<ComposerContextCompactedHint />);
    expect(container.firstChild).toBeNull();
  });
});
