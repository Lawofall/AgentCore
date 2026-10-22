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
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UserMessage } from "../UserMessage";

const { updateMessage, showFile, genMock } = vi.hoisted(() => ({
  updateMessage: vi.fn(),
  showFile: vi.fn(),
  genMock: { value: false },
}));

vi.mock("@/lib/clipboard", () => ({
  copyText: vi.fn().mockResolvedValue(true),
}));

vi.mock("@/services/turns", () => ({
  runRegenerate: vi.fn(),
}));

vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: (sel: (s: { showFile: typeof showFile }) => unknown) =>
    sel({ showFile }),
}));

vi.mock("@/hooks/useConversationFileSource", () => ({
  useConversationFileSource: () => null,
  useConversationFileSourceState: () => ({ source: null, pending: false }),
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
    useActiveGenerating: () => genMock.value,
    useConversationStore,
  };
});

afterEach(() => {
  cleanup();
  genMock.value = false;
  updateMessage.mockReset();
  showFile.mockReset();
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
            workspacePath: "attachments/brief.md",
          },
        ],
        agentMentions: [{ agentId: "w1", role: "研究员" }],
      }),
    );
    expect(screen.getByTestId("user-inline-body")).toBeTruthy();
    expect(screen.queryByTestId("user-chip-tray")).toBeNull();
    fireEvent.click(
      within(screen.getByTestId("user-inline-body")).getByRole("button", {
        name: "打开 brief.md",
      }),
    );
    expect(showFile).toHaveBeenCalledWith("attachments/brief.md", "brief.md");
    expect(screen.getAllByText("研究员")).toHaveLength(1);
    expect(screen.getAllByText("brief.md")).toHaveLength(1);
    expect(screen.getByTestId("user-inline-body").textContent).toContain(
      "按这个",
    );
    expect(screen.getByTestId("user-inline-body").textContent).not.toContain(
      "\uFFFC",
    );
  });

  it("history file chip in the tray opens the workspace File tab", () => {
    renderUser(
      userMsg({
        attachments: [
          {
            id: "att-1",
            name: "notes.md",
            path: "attachments/notes.md",
            truncated: false,
            kind: "file",
            workspacePath: "attachments/notes.md",
          },
        ],
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "打开 notes.md" }));
    expect(showFile).toHaveBeenCalledWith("attachments/notes.md", "notes.md");
  });

  it("history image is a thumb strip, not a File tab chip", () => {
    renderUser(
      userMsg({
        attachments: [
          {
            id: "att-1",
            name: "shot.png",
            path: "attachments/shot.png",
            truncated: false,
            kind: "file",
            workspacePath: "attachments/shot.png",
          },
        ],
      }),
    );
    expect(screen.getByTestId("image-thumb-strip")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "打开 shot.png" })).toBeNull();
    expect(showFile).not.toHaveBeenCalled();
  });

  it("image-only edit keeps the thumb strip and send until the last image is removed", () => {
    const png = {
      id: "att-1",
      name: "shot.png",
      path: "attachments/shot.png",
      truncated: false,
      kind: "file" as const,
      workspacePath: "attachments/shot.png",
    };
    renderUser(userMsg({ content: "", attachments: [png] }));
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(screen.getByTestId("user-inline-draft")).toBeTruthy();
    expect(screen.getByTestId("image-thumb-strip")).toBeTruthy();
    const send = screen.getByRole("button", {
      name: "发送",
    }) as HTMLButtonElement;
    expect(send.disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "移除附件" }));
    expect(send.disabled).toBe(true);
    expect(runRegenerate).not.toHaveBeenCalled();
  });

  it("edit draft file chip is not a preview control", () => {
    const content = `看${inlineToken("A", 0)}`;
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
            workspacePath: "attachments/brief.md",
          },
        ],
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    const draft = screen.getByTestId("user-inline-draft");
    expect(
      within(draft).queryByRole("button", { name: "打开 brief.md" }),
    ).toBeNull();
    expect(
      within(draft).getByRole("button", { name: "移除附件" }),
    ).toBeTruthy();
    expect(showFile).not.toHaveBeenCalled();
  });

  it("marks the bubble as a plain-copy island", () => {
    renderUser(userMsg());
    expect(
      screen.getByTestId("user-message").hasAttribute("data-copy-plain"),
    ).toBe(true);
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
    fireEvent.click(screen.getByRole("button", { name: "复制" }));
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
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
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

describe("UserMessage send chrome", () => {
  it("overlays send chrome from md up so idle actions do not pad the following reply", () => {
    renderUser(userMsg());
    const chrome = screen.getByTestId("user-message-chrome");
    expect(chrome.className).toContain("md:absolute");
    expect(chrome.className).toContain("md:top-full");
    expect(chrome.className).toContain("md:right-0");
    expect(chrome.className).toContain("md:w-max");
    expect(chrome.className).not.toContain("md:inset-x-0");
    expect(screen.getByRole("button", { name: "复制" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "编辑" })).toBeTruthy();
    expect(chrome.textContent).not.toContain("复制");
    expect(chrome.textContent).not.toContain("编辑");
  });

  it("hides send chrome while a turn is generating", () => {
    genMock.value = true;
    renderUser(userMsg({ createdAt: "2026-01-01T00:00:00Z" }));
    expect(screen.queryByTestId("user-message-chrome")).toBeNull();
    expect(screen.queryByRole("button", { name: "编辑" })).toBeNull();
  });
});
