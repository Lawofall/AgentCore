// @vitest-environment jsdom

import { FolderMembersDialog } from "@/components/folders/FolderMembersDialog";
import { searchUsers } from "@/services/messaging";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const members = vi.hoisted(() => ({
  value: [] as {
    user_id: string;
    display_name?: string | null;
    username?: string | null;
    role: "owner" | "editor" | "viewer";
    state: "pending" | "accepted";
  }[],
}));

const removeMutate = vi.hoisted(() => vi.fn());

vi.mock("@/hooks/useFolderSharing", () => ({
  useFolderMembers: () => ({
    data: members.value,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useInviteFolderMember: () => ({ mutate: vi.fn(), isPending: false }),
  useChangeFolderMemberRole: () => ({
    mutate: vi.fn(),
    isPending: false,
    variables: null,
  }),
  useRemoveOrLeaveFolderMember: () => ({
    mutate: removeMutate,
    isPending: false,
    variables: null,
  }),
}));

vi.mock("@/services/messaging", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/messaging")>();
  return { ...actual, searchUsers: vi.fn() };
});

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: (sel: (s: { user: { id: string } }) => unknown) =>
    sel({ user: { id: "me" } }),
}));

const friends = vi.hoisted(() => ({
  value: [] as { id: string; display_name: string; username: string }[],
}));

vi.mock("@/stores/messaging", () => ({
  useMessagingStore: (
    sel: (s: {
      friends: { id: string; display_name: string; username: string }[];
      friendsLoaded: boolean;
      fetchFriends: () => void;
    }) => unknown,
  ) =>
    sel({
      friends: friends.value,
      friendsLoaded: true,
      fetchFriends: vi.fn(),
    }),
}));

function renderDialog() {
  return render(
    <FolderMembersDialog
      open
      onClose={() => {}}
      folderId="folder-1"
      folderName="协作"
      myRole="owner"
    />,
  );
}

describe("FolderMembersDialog", () => {
  it("搜人失败 is muted, not destructive", async () => {
    members.value = [];
    friends.value = [];
    vi.mocked(searchUsers).mockRejectedValue(new Error("search down"));
    renderDialog();
    const input = screen.getByLabelText("按用户名或 ID 邀请成员");
    await act(async () => {
      fireEvent.change(input, { target: { value: "alice" } });
    });
    const line = await waitFor(() => screen.getByText("搜索失败，请重试"));
    expect(line.className).toContain("text-muted-foreground");
    expect(line.className).not.toContain("destructive");
  });

  it("already-member and pending friends are disabled", () => {
    members.value = [
      {
        user_id: "u-joined",
        display_name: "已加入",
        username: "joined",
        role: "editor",
        state: "accepted",
      },
      {
        user_id: "u-pending",
        display_name: "待接受",
        username: "pending",
        role: "editor",
        state: "pending",
      },
    ];
    friends.value = [
      { id: "u-joined", display_name: "已加入", username: "joined" },
      { id: "u-pending", display_name: "待接受", username: "pending" },
      { id: "u-free", display_name: "可邀请", username: "free" },
    ];
    renderDialog();
    expect(
      (screen.getByRole("button", { name: /已加入/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: /待接受/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: /可邀请/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("owner can cancel a pending invite", () => {
    members.value = [
      {
        user_id: "u-pending",
        display_name: "Alice",
        username: "alice",
        role: "editor",
        state: "pending",
      },
    ];
    friends.value = [];
    window.confirm = vi.fn(() => true);
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "取消邀请" }));
    expect(window.confirm).toHaveBeenCalled();
    expect(removeMutate).toHaveBeenCalledWith(
      { folderId: "folder-1", memberUserId: "u-pending" },
      expect.any(Object),
    );
  });
});
