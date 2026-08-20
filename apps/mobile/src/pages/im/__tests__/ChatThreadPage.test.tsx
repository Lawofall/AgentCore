// @vitest-environment jsdom
/**
 * IM 线程 chrome：icon-btn 顶栏、textarea 输入、Modal sheet；官方号无 composer。
 */
import type { ChatMessageDetail, ChatSummary } from "@/api/messaging";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  getTokens: () => ({ access_token: "a", refresh_token: "r" }),
  BASE_URL: "",
}));

const messaging = vi.hoisted(() => ({
  listMessages: vi.fn(),
  listChats: vi.fn(),
  listMembers: vi.fn(),
  markRead: vi.fn(),
  sendMessage: vi.fn(),
  uploadChatFile: vi.fn(),
  blockUser: vi.fn(),
  leaveChat: vi.fn(),
  fetchChatAttachmentBlob: vi.fn(),
}));

vi.mock("@/api/messaging", async () => {
  const actual =
    await vi.importActual<typeof import("@/api/messaging")>("@/api/messaging");
  return { ...actual, ...messaging };
});

const auth = vi.hoisted(() => ({
  me: vi.fn(async () => ({
    id: "me",
    username: "me",
    display_name: "我",
    email: null,
    created_at: "2026-01-01T00:00:00Z",
    password_must_change: false,
    role: "user",
    avatar_url: null as string | null,
  })),
}));
vi.mock("@/api/auth", () => auth);

vi.mock("@/lib/keyboardInsets", () => ({
  useKeyboardInsetBridge: vi.fn(),
}));

vi.mock("@/components/Modal", () => ({
  Modal: ({
    children,
    className,
    label,
    onClose,
  }: {
    children: ReactNode;
    className?: string;
    label?: string;
    onClose: () => void;
  }) => (
    // biome-ignore lint/a11y/useSemanticElements: Modal mock — jsdom <dialog> is not exposed as role=dialog unless open.
    <div role="dialog" className={className} aria-label={label}>
      {children}
      <button type="button" onClick={onClose} aria-label="Esc">
        Esc
      </button>
    </div>
  ),
}));

const route = vi.hoisted(() => ({
  chatId: "c1",
  chat: null as ChatSummary | null,
}));

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useNavigate: () => navigate,
    useParams: () => ({ chatId: route.chatId }),
    useLocation: () => ({
      state: route.chat ? { chat: route.chat } : {},
    }),
  };
});

import { useKeyboardInsetBridge } from "@/lib/keyboardInsets";
import { ChatThreadPage } from "@/pages/im/ChatThreadPage";

function peer(
  overrides: {
    avatar_url?: string | null;
    group_role?: "owner" | "admin" | "member";
  } = {},
) {
  return {
    id: "u2",
    username: "alice",
    display_name: "Alice",
    group_role: "member" as const,
    is_admin: false,
    muted_by_admin: false,
    online: false,
    ...overrides,
  };
}

function dmChat(): ChatSummary {
  return {
    id: "c1",
    type: "dm",
    muted: false,
    pinned: false,
    state: "accepted",
    unread: 0,
    peer: peer(),
  };
}

function officialChat(): ChatSummary {
  return {
    id: "c1",
    type: "official",
    muted: false,
    pinned: false,
    state: "accepted",
    unread: 0,
    title: "系统通知",
  };
}

function groupChat(): ChatSummary {
  return {
    id: "c1",
    type: "group",
    muted: false,
    pinned: false,
    state: "accepted",
    unread: 0,
    title: "内测群",
  };
}

function emptyPage() {
  return {
    messages: [] as ChatMessageDetail[],
    total: 0,
    page: 1,
    pageSize: 100,
  };
}

function sentMsg(content: string): ChatMessageDetail {
  return {
    id: "m-sent",
    chat_id: "c1",
    content,
    content_type: "text",
    created_at: "2026-01-01T00:00:01Z",
    sender_type: "user",
    sender_user_id: "me",
  };
}

function peerMsg(): ChatMessageDetail {
  return {
    id: "m-peer",
    chat_id: "c1",
    content: "hi",
    content_type: "text",
    created_at: "2026-01-01T00:00:02Z",
    sender_type: "user",
    sender_user_id: "u2",
  };
}

function pageOf(messages: ChatMessageDetail[]) {
  return { messages, total: messages.length, page: 1, pageSize: 100 };
}

beforeEach(() => {
  vi.clearAllMocks();
  route.chatId = "c1";
  route.chat = dmChat();
  messaging.listMessages.mockResolvedValue(emptyPage());
  messaging.listMembers.mockResolvedValue([]);
  messaging.markRead.mockResolvedValue(undefined);
  messaging.sendMessage.mockResolvedValue(sentMsg("hello"));
  Object.defineProperty(Element.prototype, "scrollTo", {
    configurable: true,
    writable: true,
    value: () => {},
  });
});

afterEach(cleanup);

describe("ChatThreadPage", () => {
  it("uses icon-btn chrome, textarea composer, and keyboard inset bridge", async () => {
    render(<ChatThreadPage />);
    expect(useKeyboardInsetBridge).toHaveBeenCalled();
    expect(screen.getByLabelText("返回").className).toMatch(/icon-btn/);
    expect(document.querySelector(".bar-title")?.textContent).toBe("Alice");
    expect(screen.getByLabelText("更多").className).toMatch(/icon-btn/);
    expect(screen.queryByText("← 消息")).toBeNull();

    const input = await screen.findByPlaceholderText("发送消息…");
    expect(input.tagName).toBe("TEXTAREA");
    expect(input.className).toMatch(/composer-input/);
  });

  it("opens the thread menu via Modal sheet and keeps DM actions", async () => {
    render(<ChatThreadPage />);
    await screen.findByPlaceholderText("发送消息…");
    fireEvent.click(screen.getByLabelText("更多"));
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toBe("sheet");
    expect(document.querySelector(".sheet-backdrop")).toBeNull();
    expect(screen.getByText("拉黑此人")).toBeTruthy();
    expect(screen.getByText("取消")).toBeTruthy();
    expect(screen.queryByText("退出会话")).toBeNull();
  });

  it("does not render a composer on official chats", async () => {
    route.chat = officialChat();
    render(<ChatThreadPage />);
    expect(await screen.findByText("系统通知")).toBeTruthy();
    expect(screen.queryByPlaceholderText("发送消息…")).toBeNull();
    expect(screen.queryByLabelText("发送")).toBeNull();

    fireEvent.click(screen.getByLabelText("更多"));
    expect(screen.getByText("取消")).toBeTruthy();
    expect(screen.queryByText("拉黑此人")).toBeNull();
    expect(screen.queryByText("退出会话")).toBeNull();
  });

  it("renders a reply quote as a two-line preview block", async () => {
    const quoted = "总是与服务器断开连接就是VPN的问题。VPN关掉就好了。";
    messaging.listMessages.mockResolvedValue({
      messages: [
        {
          id: "m-reply",
          chat_id: "c1",
          content: "设置下你的梯子，连我的服务器不开vpn",
          content_type: "text",
          created_at: "2026-01-01T00:00:02Z",
          sender_type: "user",
          sender_user_id: "me",
          reply_to_message_id: "m-orig",
          reply_to: {
            sender_user_id: "u2",
            sender_display_name: "Zoo",
            body_preview: quoted,
          },
        },
      ],
      total: 1,
      page: 1,
      pageSize: 100,
    });
    render(<ChatThreadPage />);
    const quote = await screen.findByLabelText(`回复 Zoo：${quoted}`);
    expect(quote.className).toMatch(/im-reply-quote/);
    expect(quote.querySelector(".im-reply-quote-body")?.textContent).toBe(
      quoted,
    );
  });

  it("does not badge official senders and uses the session icon", async () => {
    route.chat = {
      ...officialChat(),
      avatar_url: "/chats/official.png",
    };
    messaging.listMembers.mockResolvedValue([
      peer({
        avatar_url: "/avatars/alice.png",
        group_role: "admin",
      }),
    ]);
    messaging.listMessages.mockResolvedValue(pageOf([peerMsg()]));
    render(<ChatThreadPage />);
    await screen.findByText("hi");
    expect(screen.queryByLabelText("管理员")).toBeNull();
    expect(screen.queryByText("管理员")).toBeNull();
    expect(document.querySelector(".im-msg-avatar")?.getAttribute("src")).toBe(
      "/chats/official.png",
    );
    expect(document.body.innerHTML).not.toContain("/avatars/alice.png");
  });

  it("marks group admin next to the sender name", async () => {
    route.chat = groupChat();
    messaging.listMembers.mockResolvedValue([
      {
        id: "u2",
        username: "alice",
        display_name: "Alice",
        group_role: "admin" as const,
        is_admin: false,
        muted_by_admin: false,
        online: false,
      },
    ]);
    messaging.listMessages.mockResolvedValue({
      messages: [
        {
          id: "m-mod",
          chat_id: "c1",
          content: "hi",
          content_type: "text",
          created_at: "2026-01-01T00:00:02Z",
          sender_type: "user",
          sender_user_id: "u2",
        },
      ],
      total: 1,
      page: 1,
      pageSize: 100,
    });
    render(<ChatThreadPage />);
    expect(await screen.findByText("Alice")).toBeTruthy();
    expect(screen.getByLabelText("管理员")).toBeTruthy();
    expect(screen.getByText("管理员")).toBeTruthy();
  });

  it("renders the DM peer avatar from peer.avatar_url", async () => {
    route.chat = {
      ...dmChat(),
      peer: peer({ avatar_url: "/avatars/alice.png" }),
    };
    messaging.listMessages.mockResolvedValue(pageOf([peerMsg()]));
    render(<ChatThreadPage />);
    await screen.findByText("hi");
    const img = document.querySelector(".im-msg-avatar");
    expect(img?.tagName).toBe("IMG");
    expect(img?.getAttribute("src")).toBe("/avatars/alice.png");
  });

  it("shows initials when the DM peer has no avatar_url", async () => {
    route.chat = { ...dmChat(), avatar_url: "/chats/c1.png" };
    messaging.listMessages.mockResolvedValue(pageOf([peerMsg()]));
    render(<ChatThreadPage />);
    await screen.findByText("hi");
    const avatar = document.querySelector(".im-msg-avatar");
    expect(avatar?.tagName).toBe("SPAN");
    expect(avatar?.textContent).toBe("A");
    expect(document.querySelector("img.im-msg-avatar")).toBeNull();
    expect(document.body.innerHTML).not.toContain("/v1/users/");
    expect(document.body.innerHTML).not.toContain("/chats/c1.png");
  });

  it("renders a group sender avatar from the member DTO", async () => {
    route.chat = groupChat();
    messaging.listMembers.mockResolvedValue([
      peer({ avatar_url: "/avatars/alice.png", group_role: "admin" }),
    ]);
    messaging.listMessages.mockResolvedValue(pageOf([peerMsg()]));
    render(<ChatThreadPage />);
    await screen.findByText("hi");
    expect(document.querySelector(".im-msg-avatar")?.getAttribute("src")).toBe(
      "/avatars/alice.png",
    );
  });

  it("shows initials when a group sender has no avatar_url", async () => {
    route.chat = groupChat();
    messaging.listMembers.mockResolvedValue([peer()]);
    messaging.listMessages.mockResolvedValue(pageOf([peerMsg()]));
    render(<ChatThreadPage />);
    await screen.findByText("hi");
    const avatar = document.querySelector(".im-msg-avatar");
    expect(avatar?.tagName).toBe("SPAN");
    expect(avatar?.textContent).toBe("A");
    expect(document.body.innerHTML).not.toContain("/v1/users/");
  });

  it("renders own bubble avatar from /me avatar_url", async () => {
    auth.me.mockResolvedValueOnce({
      id: "me",
      username: "me",
      display_name: "我",
      email: null,
      created_at: "2026-01-01T00:00:00Z",
      password_must_change: false,
      role: "user",
      avatar_url: "/avatars/me.png",
    });
    messaging.listMessages.mockResolvedValue(pageOf([sentMsg("hello")]));
    render(<ChatThreadPage />);
    await screen.findByText("hello");
    expect(document.querySelector(".im-msg-avatar")?.getAttribute("src")).toBe(
      "/avatars/me.png",
    );
  });

  it("keeps the existing send path", async () => {
    render(<ChatThreadPage />);
    const input = await screen.findByPlaceholderText("发送消息…");
    fireEvent.change(input, { target: { value: "hello" } });
    fireEvent.click(screen.getByLabelText("发送"));
    await waitFor(() => {
      expect(messaging.sendMessage).toHaveBeenCalledWith(
        "c1",
        expect.objectContaining({
          content: "hello",
          contentType: "text",
        }),
      );
    });
  });
});
