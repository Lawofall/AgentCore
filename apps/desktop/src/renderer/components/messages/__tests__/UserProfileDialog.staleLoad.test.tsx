// @vitest-environment jsdom

import type { UserProfile } from "@/services/messaging";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { getUserProfile, startDm } = vi.hoisted(() => ({
  getUserProfile: vi.fn(),
  startDm: vi.fn(),
}));

vi.mock("@/services/messaging", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/messaging")>();
  return {
    ...actual,
    getUserProfile,
    startDm,
  };
});

vi.mock("@/stores/messaging", () => {
  const store = Object.assign(() => ({}), {
    subscribe: vi.fn(() => () => {}),
    getState: () => ({
      fetchFriendRequests: vi.fn(),
      fetchFriends: vi.fn(),
      upsertChat: vi.fn(),
    }),
  });
  return { useMessagingStore: store };
});

import { UserProfileDialog } from "../UserProfileDialog";

function profile(id: string, name: string): UserProfile {
  return {
    id,
    display_name: name,
    username: name.toLowerCase(),
    online: false,
    relation: "friends",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("UserProfileDialog stale load", () => {
  beforeEach(() => {
    getUserProfile.mockReset();
    startDm.mockReset();
  });

  it("切 userId 后丢弃较慢的上一份资料，操作打到当前人", async () => {
    const a = deferred<UserProfile>();
    const b = deferred<UserProfile>();
    getUserProfile.mockImplementation((id: string) => {
      if (id === "user-a") return a.promise;
      if (id === "user-b") return b.promise;
      return Promise.reject(new Error(id));
    });

    const onOpenChat = vi.fn();
    const { rerender } = render(
      <UserProfileDialog
        userId="user-a"
        open
        onClose={() => {}}
        onOpenChat={onOpenChat}
      />,
    );

    await waitFor(() => expect(getUserProfile).toHaveBeenCalledWith("user-a"));

    rerender(
      <UserProfileDialog
        userId="user-b"
        open
        onClose={() => {}}
        onOpenChat={onOpenChat}
      />,
    );
    await waitFor(() => expect(getUserProfile).toHaveBeenCalledWith("user-b"));

    await act(async () => {
      a.resolve(profile("user-a", "Alice"));
    });
    expect(screen.queryByText("Alice")).toBeNull();

    await act(async () => {
      b.resolve(profile("user-b", "Bob"));
    });
    expect(await screen.findByText("Bob")).toBeTruthy();

    startDm.mockResolvedValue({
      id: "dm-b",
      type: "dm",
      muted: false,
      pinned: false,
      state: "accepted",
      unread: 0,
    });
    await act(async () => {
      screen.getByRole("button", { name: "发消息" }).click();
    });
    await waitFor(() => expect(startDm).toHaveBeenCalledWith("user-b"));
    expect(onOpenChat).toHaveBeenCalledWith("dm-b");
  });

  it("关卡后再开另一个人，在途的上一份不得填回", async () => {
    const a = deferred<UserProfile>();
    const b = deferred<UserProfile>();
    getUserProfile.mockImplementation((id: string) => {
      if (id === "user-a") return a.promise;
      if (id === "user-b") return b.promise;
      return Promise.reject(new Error(id));
    });

    const { rerender } = render(
      <UserProfileDialog
        userId="user-a"
        open
        onClose={() => {}}
        onOpenChat={() => {}}
      />,
    );
    await waitFor(() => expect(getUserProfile).toHaveBeenCalledWith("user-a"));

    rerender(
      <UserProfileDialog
        userId={null}
        open={false}
        onClose={() => {}}
        onOpenChat={() => {}}
      />,
    );
    rerender(
      <UserProfileDialog
        userId="user-b"
        open
        onClose={() => {}}
        onOpenChat={() => {}}
      />,
    );
    await waitFor(() => expect(getUserProfile).toHaveBeenCalledWith("user-b"));

    await act(async () => {
      a.resolve(profile("user-a", "Alice"));
    });
    expect(screen.queryByText("Alice")).toBeNull();

    await act(async () => {
      b.resolve(profile("user-b", "Bob"));
    });
    expect(await screen.findByText("Bob")).toBeTruthy();
  });
});
