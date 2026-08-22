// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/narrowLayout", () => ({
  useNarrowLayoutState: () => ({
    isNarrow: true,
    hideChrome: false,
    conversationDrawerOpen: false,
    setConversationDrawerOpen: () => undefined,
  }),
}));

vi.mock("@/components/messages/ChatList", () => ({
  ChatList: ({ className }: { className?: string }) => (
    <div data-testid="chat-list" className={className} />
  ),
}));
vi.mock("@/components/messages/ChatThread", () => ({
  ChatThread: () => <div data-testid="chat-thread" />,
}));
vi.mock("@/components/messages/ContactsDialog", () => ({
  ContactsDialog: () => null,
}));
vi.mock("@/components/messages/NewChatDialog", () => ({
  NewChatDialog: () => null,
}));
vi.mock("@/components/messages/ProductNoticeDetail", () => ({
  ProductNoticeDetail: () => <div data-testid="notice-detail" />,
}));
vi.mock("@/components/messages/UserProfileDialog", () => ({
  UserProfileDialog: () => null,
}));
vi.mock("@/stores/messaging", () => ({
  useMessagingStore: Object.assign(
    (
      sel: (s: {
        profileUserId: null;
        openProfile: () => void;
        closeProfile: () => void;
        fetchFriendRequests: () => void;
        setActiveChat: () => void;
        openChat: () => void;
      }) => unknown,
    ) =>
      sel({
        profileUserId: null,
        openProfile: () => undefined,
        closeProfile: () => undefined,
        fetchFriendRequests: () => undefined,
        setActiveChat: () => undefined,
        openChat: () => undefined,
      }),
    {
      getState: () => ({
        setActiveChat: () => undefined,
        openChat: () => undefined,
        activeChatId: null,
      }),
    },
  ),
}));

import { MessagesPage } from "../MessagesPage";

afterEach(() => {
  cleanup();
});

describe("MessagesPage narrow", () => {
  it("shows only the list on /messages", () => {
    render(
      <MemoryRouter initialEntries={["/messages"]}>
        <Routes>
          <Route path="/messages" element={<MessagesPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("chat-list").className).toContain("w-full");
    expect(screen.queryByTestId("chat-thread")).toBeNull();
    expect(screen.queryByText("选择一个会话，或发起新会话")).toBeNull();
  });

  it("shows only the thread on /messages/:id", () => {
    render(
      <MemoryRouter initialEntries={["/messages/c1"]}>
        <Routes>
          <Route path="/messages/:chatId" element={<MessagesPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("chat-list").className).toContain("hidden");
    expect(screen.getByTestId("chat-thread")).toBeTruthy();
  });
});
