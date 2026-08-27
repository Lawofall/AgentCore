// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import { copyText } from "@/lib/clipboard";
import {
  dropInlineIndex,
  inlineToken,
  renderInlineLabels,
} from "@/lib/inlineBody";
import { runRegenerate } from "@/services/turns";
import type { Message } from "@/stores/conversation";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UserMessage } from "../UserMessage";

const { updateMessage } = vi.hoisted(() => ({
  updateMessage: vi.fn(),
}));

vi.mock("@/lib/clipboard", () => ({
  copyText: vi.fn().mockResolvedValue(true),
}));

vi.mock("@/services/turns", () => ({
  runRegenerate: vi.fn(),
}));

vi.mock("@/stores/conversation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/conversation")>();
  const storeApi = {
    currentConversationId: "c1",
    updateMessage,
  };
  const useConversationStore = Object.assign(
    (sel: (s: typeof storeApi) => unknown) => sel(storeApi),
    { getState: () => storeApi },
  );
  return {
    ...actual,
    useActiveGenerating: () => false,
    useConversationStore,
  };
});

afterEach(() => {
  cleanup();
  updateMessage.mockReset();
  vi.mocked(copyText).mockClear();
  vi.mocked(runRegenerate).mockClear();
});

function userMsg(over: Partial<Message> = {}): Message {
  return {
    id: "u1",
    role: "user",
    content: "帮我调研",
    createdAt: "2026-01-01T00:00:00Z",
    executionId: null,
    isStreaming: false,
    ...over,
  };
}

function renderUser(message: Message) {
  return render(
    <TooltipProvider>
      <UserMessage message={message} />
    </TooltipProvider>,
  );
}

describe("UserMessage agent mention chips", () => {
  it("replays persisted @ role chips on the history bubble", () => {
    renderUser(
      userMsg({
        agentMentions: [{ agentId: "w1", role: "研究员" }],
      }),
    );
    expect(screen.getByText("研究员")).toBeTruthy();
    expect(screen.getByText("点名")).toBeTruthy();
    expect(screen.getByText("帮我调研")).toBeTruthy();
    expect(screen.getByTestId("user-chip-tray")).toBeTruthy();
    expect(screen.queryByTestId("user-inline-body")).toBeNull();
  });

  it("renders marked body pills inline and skips the tray", () => {
    const content = `按这个${inlineToken("A", 0)}请${inlineToken("M", 0)}看`;
    renderUser(
      userMsg({
        content,
        attachments: [
          {
            id: "att-1",
            name: "brief.md",
            path: "brief.md",
            truncated: false,
            kind: "file",
          },
        ],
        agentMentions: [{ agentId: "w1", role: "研究员" }],
      }),
    );
    expect(screen.getByTestId("user-inline-body")).toBeTruthy();
    expect(screen.queryByTestId("user-chip-tray")).toBeNull();
    expect(screen.getAllByText("研究员")).toHaveLength(1);
    expect(screen.getAllByText("brief.md")).toHaveLength(1);
    expect(screen.getByTestId("user-inline-body").textContent).toContain(
      "按这个",
    );
    expect(screen.getByTestId("user-inline-body").textContent).not.toContain(
      "\uFFFC",
    );
  });

  it("copies human labels, not U+FFFC markers", async () => {
    const content = `按这个${inlineToken("A", 0)}请${inlineToken("M", 0)}看`;
    const attachments = [
      {
        id: "att-1",
        name: "brief.md",
        path: "brief.md",
        truncated: false,
        kind: "file" as const,
      },
    ];
    const agentMentions = [{ agentId: "w1", role: "研究员" }];
    renderUser(userMsg({ content, attachments, agentMentions }));
    fireEvent.click(screen.getByText("复制"));
    await waitFor(() => {
      expect(copyText).toHaveBeenCalledWith(
        renderInlineLabels(content, attachments, agentMentions),
      );
    });
    expect(vi.mocked(copyText).mock.calls[0]?.[0]).not.toContain("\uFFFC");
  });

  it("edits inline without exposing markers; drop rewrites content and materials", () => {
    const content = `请${inlineToken("M", 0)}看`;
    renderUser(
      userMsg({
        content,
        agentMentions: [{ agentId: "w1", role: "研究员" }],
      }),
    );
    fireEvent.click(screen.getByText("编辑"));
    expect(screen.getByTestId("user-inline-draft")).toBeTruthy();
    expect(
      screen.getByTestId("user-inline-draft").textContent ?? "",
    ).not.toContain("\uFFFC");
    fireEvent.click(screen.getByRole("button", { name: "移除角色点名" }));
    fireEvent.click(screen.getByText("发送"));
    expect(updateMessage).toHaveBeenCalledWith(
      "u1",
      {
        content: dropInlineIndex(content, "mention", 0),
        attachments: [],
        agentMentions: [],
      },
      "c1",
    );
    expect(runRegenerate).toHaveBeenCalledWith(
      "u1",
      dropInlineIndex(content, "mention", 0),
      { attachments: [], agentMentions: [] },
    );
  });
});
