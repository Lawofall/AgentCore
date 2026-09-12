// @vitest-environment jsdom
/**
 * Regression tests for the admin 内测群 page.
 *
 * 任命 used to accept whatever sat in the ID box — a username, a stale value, or
 * someone who already had the role — and the operator only found out from a raw API
 * error. Picking a user from the search box also dropped a bare UUID into the field
 * with no sign of who it was. These pin the guard rails plus the revoke confirmation.
 * The leading block comment keeps the @vitest-environment directive file-leading.
 */

import { BetaGroupPage } from "@/pages/BetaGroupPage";
import {
  type BetaGroupModerator,
  type BetaGroupModeratorsResponse,
  appointBetaGroupModerator,
  listBetaGroupModerators,
  revokeBetaGroupModerator,
} from "@/services/adminBetaGroup";
import {
  type AdminUserListItem,
  type AdminUserListResponse,
  listUsers,
} from "@/services/adminUsers";
import { ApiError } from "@/services/api";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

vi.mock("@/services/adminBetaGroup", () => ({
  listBetaGroupModerators: vi.fn(),
  appointBetaGroupModerator: vi.fn(),
  revokeBetaGroupModerator: vi.fn(),
}));
vi.mock("@/services/adminUsers", () => ({ listUsers: vi.fn() }));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function moderator(
  p: Partial<BetaGroupModerator> & { id: string; username: string },
): BetaGroupModerator {
  return { display_name: "", is_platform_admin: false, ...p };
}

function rosterResp(
  data: BetaGroupModerator[],
  total = data.length,
): BetaGroupModeratorsResponse {
  return { chat_id: "chat-beta-1", data, title: "AgentCore 内测群", total };
}

function userHit(
  p: Partial<AdminUserListItem> & { id: string; username: string },
): AdminUserListItem {
  return {
    cost_cny_total: 0,
    created_at: "2026-07-01T00:00:00Z",
    display_name: "",
    email: null,
    last_active_at: null,
    role: "user",
    status: "active",
    ...p,
  } as AdminUserListItem;
}

function usersResp(data: AdminUserListItem[]): AdminUserListResponse {
  return { data, total: data.length, page: 1, page_size: 8 } as AdminUserListResponse;
}

function renderPage() {
  return render(
    <MemoryRouter>
      <BetaGroupPage />
    </MemoryRouter>,
  );
}

describe("BetaGroupPage", () => {
  it("渲染管理员名册与群信息", async () => {
    vi.mocked(listBetaGroupModerators).mockResolvedValue(
      rosterResp([
        moderator({ id: "u1", username: "alice", display_name: "爱丽丝" }),
        moderator({ id: "u2", username: "root", is_platform_admin: true }),
      ]),
    );

    renderPage();

    expect(await screen.findByText("爱丽丝")).toBeTruthy();
    expect(screen.getByText("AgentCore 内测群")).toBeTruthy();
    expect(screen.getByText("chat-beta-1")).toBeTruthy();
    expect(screen.getByText(/共 2 人/)).toBeTruthy();
    expect(
      within(screen.getByRole("table")).getByText(/平台 admin/),
    ).toBeTruthy();
  });

  it("撤销要先确认，确认后才调接口", async () => {
    vi.mocked(listBetaGroupModerators).mockResolvedValue(
      rosterResp([moderator({ id: "u1", username: "alice", display_name: "爱丽丝" })]),
    );
    vi.mocked(revokeBetaGroupModerator).mockResolvedValue({ status: "ok" });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /撤销/ }));
    expect(vi.mocked(revokeBetaGroupModerator)).not.toHaveBeenCalled();

    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText(/不会影响其平台账号角色/)).toBeTruthy();
    fireEvent.click(dialog.getByRole("button", { name: /确认撤销/ }));

    await waitFor(() =>
      expect(vi.mocked(revokeBetaGroupModerator)).toHaveBeenCalledWith("u1"),
    );
  });

  it("用户名形态的输入不会打到接口", async () => {
    vi.mocked(listBetaGroupModerators).mockResolvedValue(rosterResp([]));
    renderPage();
    await screen.findByText("还没有内测群管理员");

    fireEvent.change(screen.getByLabelText("用户 ID"), {
      target: { value: "@alice" },
    });
    fireEvent.click(screen.getByRole("button", { name: /任命/ }));

    expect(vi.mocked(appointBetaGroupModerator)).not.toHaveBeenCalled();
    expect(vi.mocked(toast.error)).toHaveBeenCalledWith(
      expect.stringContaining("用户名"),
    );
  });

  it("已经是管理员的用户不会被重复任命", async () => {
    vi.mocked(listBetaGroupModerators).mockResolvedValue(
      rosterResp([moderator({ id: "u1", username: "alice" })]),
    );
    renderPage();
    await screen.findByText("@alice");

    fireEvent.change(screen.getByLabelText("用户 ID"), {
      target: { value: "u1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /任命/ }));

    expect(vi.mocked(appointBetaGroupModerator)).not.toHaveBeenCalled();
    expect(vi.mocked(toast.error)).toHaveBeenCalledWith(
      expect.stringContaining("已经是内测群管理员"),
    );
  });

  it("搜索选中后显示选中的是谁，而不是只剩一串 ID", async () => {
    vi.mocked(listBetaGroupModerators).mockResolvedValue(rosterResp([]));
    vi.mocked(listUsers).mockResolvedValue(
      usersResp([userHit({ id: "u9", username: "bob", display_name: "鲍勃" })]),
    );

    renderPage();
    await screen.findByText("还没有内测群管理员");

    fireEvent.change(screen.getByLabelText("搜索用户"), {
      target: { value: "bob" },
    });

    const hit = await screen.findByRole("button", { name: /鲍勃/ });
    fireEvent.click(hit);

    expect((screen.getByLabelText("用户 ID") as HTMLInputElement).value).toBe("u9");
    expect(screen.getByText(/已选中/).textContent).toContain("鲍勃");
  });

  /**
   * `total` 起手是 0，直接印进标题就会在名册压根没读到时写「共 0 人」——和正文的红字
   * 报错同框，截图上报时会被读成「管理员全没了」。
   */
  it("名册加载失败时标题写「共 —」而不是「共 0 人」", async () => {
    vi.mocked(listBetaGroupModerators).mockRejectedValue(
      new ApiError(503, JSON.stringify({ error: { message: "后端不可达" } })),
    );

    renderPage();

    expect(await screen.findByText("后端不可达")).toBeTruthy();
    expect(screen.getByText(/共 — 人/)).toBeTruthy();
    expect(screen.queryByText(/共 0 人/)).toBeNull();
  });

  it("空态说明平台 admin 无需任命", async () => {
    vi.mocked(listBetaGroupModerators).mockResolvedValue(rosterResp([]));
    renderPage();

    expect(await screen.findByText("还没有内测群管理员")).toBeTruthy();
    expect(screen.getByText("只列群内管理员；平台 admin 不必任命。")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    // 读到了、确实是 0：这时候「共 0 人」才是事实，不该退化成「—」。
    expect(screen.getByText(/共 0 人/)).toBeTruthy();
  });
});
