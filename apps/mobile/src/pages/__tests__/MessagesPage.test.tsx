// @vitest-environment jsdom
/**
 * 消息 tab 根页 chrome：标题靠左，右侧「发起」是 icon-btn（不是小字 link）。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  getTokens: () => ({ access_token: "a", refresh_token: "r" }),
  BASE_URL: "",
}));

vi.mock("@/api/auth", () => ({
  me: vi.fn().mockRejectedValue(new Error("unused")),
}));

const { listChats } = vi.hoisted(() => ({
  listChats: vi.fn(),
}));
vi.mock("@/api/messaging", async () => {
  const actual =
    await vi.importActual<typeof import("@/api/messaging")>("@/api/messaging");
  return { ...actual, listChats };
});

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return { ...actual, useNavigate: () => navigate };
});

import { MessagesPage } from "@/pages/MessagesPage";

beforeEach(() => {
  vi.clearAllMocks();
  listChats.mockResolvedValue([]);
});

afterEach(cleanup);

describe("MessagesPage", () => {
  it("keeps the title left and uses an icon-btn for 发起", async () => {
    render(<MessagesPage />);
    expect(screen.getByText("消息")).toBeTruthy();
    expect(document.querySelector(".bar-title")).toBeNull();

    const start = screen.getByRole("button", { name: "发起" });
    expect(start.className).toMatch(/icon-btn/);
    expect(start.textContent).not.toMatch(/发起/);

    fireEvent.click(start);
    expect(navigate).toHaveBeenCalledWith("/im/new");

    expect(
      await screen.findByText("还没有会话。点右上角发起新聊天。"),
    ).toBeTruthy();
  });

  it("uses peer.avatar_url for DM rows and ignores chat.avatar_url", async () => {
    const peer = {
      id: "u2",
      username: "alice",
      display_name: "Alice",
      group_role: "member" as const,
      is_admin: false,
      muted_by_admin: false,
      online: false,
    };
    listChats.mockResolvedValue([
      {
        id: "c1",
        type: "dm",
        muted: false,
        pinned: false,
        state: "accepted",
        unread: 0,
        avatar_url: "/chats/c1.png",
        peer: { ...peer, avatar_url: "/avatars/alice.png" },
      },
      {
        id: "c2",
        type: "dm",
        muted: false,
        pinned: false,
        state: "accepted",
        unread: 0,
        avatar_url: "/chats/c2.png",
        peer: { ...peer, id: "u3", username: "bob", display_name: "Bob" },
      },
    ]);
    render(<MessagesPage />);
    await screen.findByText("Alice");
    const rows = document.querySelectorAll(".im-row");
    expect(rows[0]?.querySelector("img")?.getAttribute("src")).toBe(
      "/avatars/alice.png",
    );
    const bob = rows[1]?.querySelector(".im-avatar");
    expect(bob?.tagName).toBe("SPAN");
    expect(bob?.textContent).toBe("B");
    expect(document.body.innerHTML).not.toContain("/chats/c2.png");
    expect(document.body.innerHTML).not.toContain("/v1/users/");
  });

  it("uses chat.avatar_url as the group session icon", async () => {
    listChats.mockResolvedValue([
      {
        id: "g1",
        type: "group",
        muted: false,
        pinned: false,
        state: "accepted",
        unread: 0,
        title: "内测群",
        avatar_url: "/chats/g1.png",
      },
      {
        id: "g2",
        type: "group",
        muted: false,
        pinned: false,
        state: "accepted",
        unread: 0,
        title: "无图标群",
      },
    ]);
    render(<MessagesPage />);
    await screen.findByText("内测群");
    const rows = document.querySelectorAll(".im-row");
    expect(rows[0]?.querySelector("img")?.getAttribute("src")).toBe(
      "/chats/g1.png",
    );
    expect(rows[1]?.querySelector(".im-avatar")?.tagName).toBe("SPAN");
    expect(rows[1]?.querySelector(".im-avatar")?.textContent).toBe("无");
  });
});
