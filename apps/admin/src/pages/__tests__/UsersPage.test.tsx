// @vitest-environment jsdom
/**
 * Render tests for the admin 用户管理 page (AUD-012 测试覆盖补强 · admin 半).
 *
 * UsersPage owns the roster table + row actions (改角色 / 停用·启用 / 注销 / 配额) and keeps its
 * filters in the query string. These pin its happy paths and the key self-guards with the
 * services mocked (no real HTTP) and the not-under-test leaves (UserDetail / QuotaDialog)
 * stubbed, so the test targets the page's own list rendering, self-row guarding, status
 * toggle, empty + error/retry branches, and the URL-backed filter contract (a shared link
 * reopens the same view, a filter change lands on page 1). The leading block comment keeps
 * the @vitest-environment directive file-leading.
 */

import { UsersPage } from "@/pages/UsersPage";
import {
  type AdminUser,
  type AdminUserListItem,
  type AdminUserListResponse,
  deleteUser,
  listUsers,
  updateUser,
} from "@/services/adminUsers";
import { ApiError } from "@/services/api";
import { useAuthStore } from "@/stores/auth";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/adminUsers", () => ({
  listUsers: vi.fn(),
  updateUser: vi.fn(),
  deleteUser: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
// Not under test here — stubbed so the roster test never pulls their module graph.
vi.mock("@/components/UserDetail", () => ({
  UserDetail: () => <div data-testid="user-detail" />,
}));
vi.mock("@/components/QuotaDialog", () => ({
  QuotaDialog: () => <div data-testid="quota-dialog" />,
}));

const SELF_ID = "self-id";

beforeEach(() => {
  useAuthStore.setState({
    status: "authenticated",
    user: {
      id: SELF_ID,
      username: "admin",
      displayName: "管理员",
      email: null,
      emailVerifiedAt: null,
      role: "admin",
      passwordMustChange: false,
    },
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function userItem(
  p: Partial<AdminUserListItem> & { id: string },
): AdminUserListItem {
  return {
    username: "user",
    display_name: "",
    email: null,
    role: "user",
    status: "active",
    deleted_at: null,
    is_unlimited: false,
    quota_daily_tokens: null,
    quota_daily_requests: null,
    quota_monthly_cost_cny: null,
    quota_daily_cost_cny: null,
    cost_total: 0,
    created_at: "2026-06-01T00:00:00Z",
    ...p,
  };
}

function listResp(
  data: AdminUserListItem[],
  total = data.length,
): AdminUserListResponse {
  return { data, total, page: 1, page_size: 20 };
}

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{`${loc.pathname}${loc.search}`}</div>;
}

function renderUsers(initialEntry = "/users") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <LocationProbe />
      <Routes>
        <Route path="/users" element={<UsersPage />} />
        <Route path="/users/:userId" element={<UsersPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** The link an operator would copy right now. */
const currentUrl = () => screen.getByTestId("loc").textContent;
const lastQuery = () => vi.mocked(listUsers).mock.calls.at(-1)?.[0];

describe("UsersPage", () => {
  it("renders the roster from listUsers (names, self marker, count)", async () => {
    vi.mocked(listUsers).mockResolvedValue(
      listResp(
        [
          userItem({ id: SELF_ID, username: "alice", display_name: "Alice" }),
          userItem({ id: "u2", username: "bob", display_name: "Bob" }),
        ],
        2,
      ),
    );
    renderUsers();
    expect(await screen.findByText("Alice")).toBeTruthy();
    expect(screen.getByText("Bob")).toBeTruthy();
    expect(screen.getByText(/@bob/)).toBeTruthy();
    expect(screen.getByText("(我)")).toBeTruthy(); // the signed-in admin's own row
    expect(screen.getByText(/共 2 个账号/)).toBeTruthy();
  });

  it("guards self-row actions (role select + 停用 disabled for the signed-in admin)", async () => {
    vi.mocked(listUsers).mockResolvedValue(
      listResp([
        userItem({ id: SELF_ID, username: "alice", display_name: "Alice", role: "admin" }),
      ]),
    );
    renderUsers();
    await screen.findByText("Alice");
    expect((screen.getByDisplayValue("admin") as HTMLSelectElement).disabled).toBe(true);
    expect(
      (screen.getByRole("button", { name: "停用" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("停用 a non-self user → updateUser + toast + the row flips to 启用", async () => {
    vi.mocked(listUsers).mockResolvedValue(
      listResp(
        [
          userItem({ id: SELF_ID, username: "alice", display_name: "Alice", role: "admin" }),
          userItem({ id: "u2", username: "bob", display_name: "Bob" }),
        ],
        2,
      ),
    );
    vi.mocked(updateUser).mockResolvedValue({
      id: "u2",
      status: "disabled",
    } as unknown as AdminUser);
    renderUsers();
    await screen.findByText("Bob");
    // alice (self) 停用 is disabled; bob's is the enabled one.
    const enabled = screen
      .getAllByRole("button", { name: "停用" })
      .find((b) => !(b as HTMLButtonElement).disabled);
    fireEvent.click(enabled as HTMLButtonElement);
    await waitFor(() =>
      expect(updateUser).toHaveBeenCalledWith("u2", { status: "disabled" }),
    );
    expect(toast.success).toHaveBeenCalledWith("账号已停用");
    expect(await screen.findByRole("button", { name: "启用" })).toBeTruthy();
  });

  it("shows the empty state when no users match", async () => {
    vi.mocked(listUsers).mockResolvedValue(listResp([], 0));
    renderUsers();
    expect(await screen.findByText("没有匹配的用户")).toBeTruthy();
    // A settled zero *is* the count: only an unknown total may fall back to「—」.
    expect(screen.getByText(/共 0 个账号/)).toBeTruthy();
    expect(deleteUser).not.toHaveBeenCalled();
  });

  /**
   * `total` starts at 0, so a header built straight off it announces「共 0 个账号」
   * over the skeleton and again over a red error line — an ops screenshot of either
   * reads as "every account is gone".
   */
  it("首屏还没落地时标题写「共 —」而不是「共 0 个账号」", async () => {
    vi.mocked(listUsers).mockReturnValue(
      new Promise<AdminUserListResponse>(() => {}),
    );
    renderUsers();
    expect(await screen.findByText(/共 — 个账号/)).toBeTruthy();
    expect(screen.queryByText(/共 0 个账号/)).toBeNull();
    // Still the skeleton: nothing has come back to count yet.
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("首屏加载失败时标题也不写「共 0 个账号」", async () => {
    vi.mocked(listUsers).mockRejectedValue(
      new ApiError(500, JSON.stringify({ error: { message: "服务器开小差" } })),
    );
    renderUsers();
    expect(await screen.findByText("服务器开小差")).toBeTruthy();
    expect(screen.getByText(/共 — 个账号/)).toBeTruthy();
    expect(screen.queryByText(/共 0 个账号/)).toBeNull();
  });

  it("配额列只列覆盖过的维度，且大数字带千分位", async () => {
    vi.mocked(listUsers).mockResolvedValue(
      listResp(
        [
          userItem({
            id: "u1",
            username: "capped",
            display_name: "Capped",
            quota_daily_tokens: 20000000,
          }),
          userItem({ id: "u2", username: "plain", display_name: "Plain" }),
        ],
        2,
      ),
    );
    renderUsers();
    // 「日 20000000 token · 月 继承 · 继承 请求」——裸数字，「继承」还出现两次。
    expect(
      await screen.findByText("日 20,000,000 token · 其余继承"),
    ).toBeTruthy();
    expect(screen.getByText("继承默认")).toBeTruthy();
  });

  it("surfaces a load error then recovers on 重试", async () => {
    vi.mocked(listUsers)
      .mockRejectedValueOnce(
        new ApiError(500, JSON.stringify({ error: { message: "服务器开小差" } })),
      )
      .mockResolvedValue(listResp([userItem({ id: "u2", display_name: "Bob" })], 1));
    renderUsers();
    expect(await screen.findByText("服务器开小差")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("Bob")).toBeTruthy();
  });

  it("末页删光后钳回合法页并重新拉列表", async () => {
    const page2Only = userItem({
      id: "u21",
      username: "last",
      display_name: "LastOnPage2",
    });
    const page1Users = Array.from({ length: 20 }, (_, i) =>
      userItem({
        id: `u${i + 1}`,
        username: `user${i + 1}`,
        display_name: `User${i + 1}`,
      }),
    );
    vi.mocked(listUsers).mockImplementation(async (opts) => {
      if (opts.page === 2) return listResp([page2Only], 21);
      return listResp(page1Users, 20);
    });
    vi.mocked(deleteUser).mockResolvedValue({
      id: "u21",
      deleted_at: "2026-08-06T00:00:00Z",
    } as unknown as AdminUser);

    renderUsers("/users?page=2");
    expect(await screen.findByText("LastOnPage2")).toBeTruthy();
    // Pager 口径: 页码步进 + 常显总数 (Pagination hides the stepper at 1 page).
    expect(screen.getByText("2 / 2")).toBeTruthy();
    expect(screen.getByText(/共 21 条/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "注销" }));
    fireEvent.click(screen.getByRole("button", { name: "确认注销" }));

    await waitFor(() => {
      expect(deleteUser).toHaveBeenCalledWith("u21");
      expect(screen.getByText(/共 20 条/)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "下一页" })).toBeNull();
      expect(screen.getByText("User1")).toBeTruthy();
      expect(screen.queryByText("LastOnPage2")).toBeNull();
      expect(screen.queryByText("没有匹配的用户")).toBeNull();
    });
    expect(vi.mocked(listUsers).mock.calls.some((c) => c[0].page === 1)).toBe(
      true,
    );
  });

  /**
   * Out-of-order responses are covered by 「连切两次筛选」below. This one is now purely
   * about the clamp: before filters lived in the URL, searching from page 2 left the
   * request on `page=2` while the table claimed page 1. The intermediate `page=2 + q`
   * fetch this used to race against no longer exists by design — `set()` drops `?page=`
   * in the same navigation — so asserting on a stale response here would assert on
   * nothing.
   */
  it("search on ?page>1 clamps both the request and the table back to page 1", async () => {
    type Deferred = {
      resolve: (v: AdminUserListResponse) => void;
      promise: Promise<AdminUserListResponse>;
    };
    const deferreds: Deferred[] = [];
    vi.mocked(listUsers).mockImplementation(() => {
      let resolve!: (v: AdminUserListResponse) => void;
      const promise = new Promise<AdminUserListResponse>((r) => {
        resolve = r;
      });
      deferreds.push({ resolve, promise });
      return promise;
    });

    renderUsers("/users?page=2");
    await waitFor(() => expect(deferreds.length).toBe(1));
    deferreds[0]!.resolve(
      listResp(
        [userItem({ id: "p2", username: "page2user", display_name: "Page2User" })],
        40,
      ),
    );
    expect(await screen.findByText("Page2User")).toBeTruthy();
    expect(screen.getByText("2 / 2")).toBeTruthy();
    expect(screen.getByText(/共 40 条/)).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("搜索用户名 / 昵称"), {
      target: { value: "alice" },
    });

    // Debounce (300ms) → filter effect may load page=2+q before setPage(1) settles.
    // Wait until the clamped page=1 fetch is actually in flight (not merely ≥2 calls).
    await waitFor(
      () => {
        const last = vi.mocked(listUsers).mock.calls.at(-1)?.[0];
        expect(last?.page).toBe(1);
        expect(last?.q).toBe("alice");
      },
      { timeout: 2000 },
    );

    deferreds[deferreds.length - 1]!.resolve(
      listResp(
        [userItem({ id: "a1", username: "alice", display_name: "AliceHit" })],
        1,
      ),
    );

    expect(await screen.findByText("AliceHit")).toBeTruthy();
    expect(screen.queryByText("Page2User")).toBeNull();
    // The page-2 total must not survive alongside the page-1 rows.
    expect(screen.getByText(/共 1 条/)).toBeTruthy();
    expect(screen.queryByText(/共 40 条/)).toBeNull();
  });

  it("注销确认走统一模态：role=dialog、说清后果、Esc 可取消", async () => {
    vi.mocked(listUsers).mockResolvedValue(
      listResp([userItem({ id: "u2", username: "bob", display_name: "Bob" })]),
    );
    renderUsers();
    await screen.findByText("Bob");

    fireEvent.click(screen.getByRole("button", { name: "注销" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    // 后果必须写明，而不是只问「确定吗」。
    expect(dialog.textContent).toContain("匿名化");
    expect(dialog.textContent).toContain("所有设备立即登出");

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(deleteUser).not.toHaveBeenCalled();
  });

  it("带筛选参数的链接直接打开即复现该视图（首个请求 + 控件回显）", async () => {
    vi.mocked(listUsers).mockResolvedValue(
      listResp([userItem({ id: "u2", username: "bob", display_name: "Bob" })]),
    );

    renderUsers(
      "/users?q=alice&ip=10.0.0.1&since=2026-06-01&until=2026-06-30" +
        "&role=admin&status=disabled&sort=cost&order=asc&include_deleted=1&page=2",
    );
    await screen.findByText("Bob");

    // The *first* request already carries the whole shared view: no unfiltered flash
    // that the filters then narrow, and no second round trip to get there.
    expect(vi.mocked(listUsers).mock.calls.length).toBe(1);
    expect(lastQuery()).toEqual({
      page: 2,
      pageSize: 20,
      q: "alice",
      ip: "10.0.0.1",
      since: "2026-06-01T00:00:00.000Z",
      until: "2026-06-30T23:59:59.999Z",
      role: "admin",
      status: "disabled",
      sort: "cost",
      order: "asc",
      includeDeleted: true,
    });

    // …and the controls show what the link asked for, so the recipient can see which
    // filters are narrowing the roster instead of guessing.
    const value = (label: string) =>
      (screen.getByLabelText(label) as HTMLInputElement | HTMLSelectElement).value;
    expect(value("搜索用户名 / 昵称")).toBe("alice");
    expect(value("按 IP 筛选")).toBe("10.0.0.1");
    expect(value("按角色筛选")).toBe("admin");
    expect(value("按状态筛选")).toBe("disabled");
    expect(value("注册起始日期")).toBe("2026-06-01");
    expect(value("注册截止日期")).toBe("2026-06-30");
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
    // 累计成本 ascending is the active sort, not the default 注册时间 desc.
    expect(screen.getByRole("button", { name: "按累计成本排序" })).toBeTruthy();
  });

  it("改筛选回到第一页，且 ?page= 与筛选在同一次导航里更新", async () => {
    vi.mocked(listUsers).mockResolvedValue(
      listResp([userItem({ id: "u2", display_name: "Bob" })], 40),
    );

    renderUsers("/users?q=alice&page=2");
    await screen.findByText("Bob");
    expect(lastQuery()?.page).toBe(2);

    fireEvent.change(screen.getByLabelText("按角色筛选"), {
      target: { value: "admin" },
    });

    await waitFor(() => {
      expect(lastQuery()?.page).toBe(1);
      expect(lastQuery()?.role).toBe("admin");
    });
    // Page 2 of the old result set is meaningless under the new filter, and the link
    // the operator can copy now points at the narrowed view's first page.
    expect(currentUrl()).toBe("/users?q=alice&role=admin");
    // Writing page and filter separately would fire an extra old-page + new-filter load.
    expect(
      vi.mocked(listUsers).mock.calls.filter((c) => c[0].role === "admin").length,
    ).toBe(1);
  });

  it("排序也在 URL 里：点列头换排序键并回到第一页", async () => {
    vi.mocked(listUsers).mockResolvedValue(
      listResp([userItem({ id: "u2", display_name: "Bob" })], 40),
    );

    renderUsers("/users?page=3");
    await screen.findByText("Bob");

    fireEvent.click(screen.getByRole("button", { name: "按累计成本排序" }));
    await waitFor(() => expect(lastQuery()?.sort).toBe("cost"));
    expect(lastQuery()?.order).toBe("desc");
    expect(lastQuery()?.page).toBe(1);
    // `order=desc` is the default and stays out of the URL.
    expect(currentUrl()).toBe("/users?sort=cost");

    fireEvent.click(screen.getByRole("button", { name: "按累计成本排序" }));
    await waitFor(() => expect(lastQuery()?.order).toBe("asc"));
    expect(currentUrl()).toBe("/users?sort=cost&order=asc");
  });

  it("搜索防抖：输入即时回显，URL 稍后跟上，不失焦也不吞按键", async () => {
    vi.mocked(listUsers).mockResolvedValue(
      listResp([userItem({ id: "u2", display_name: "Bob" })]),
    );

    renderUsers();
    await screen.findByText("Bob");
    const box = screen.getByLabelText("搜索用户名 / 昵称") as HTMLInputElement;
    box.focus();

    fireEvent.change(box, { target: { value: "a" } });
    fireEvent.change(box, { target: { value: "al" } });
    fireEvent.change(box, { target: { value: "ali" } });
    expect(box.value).toBe("ali");

    await waitFor(() => expect(currentUrl()).toBe("/users?q=ali"), {
      timeout: 2000,
    });
    expect(document.activeElement).toBe(box);
    // Three keystrokes collapse into one query. Asserting "the URL has not moved yet"
    // immediately after a keystroke would instead race the real 300ms timer, and loses
    // that race whenever the suite runs under load.
    const queried = vi.mocked(listUsers).mock.calls.map((c) => c[0].q);
    expect(queried).not.toContain("a");
    expect(queried).not.toContain("al");

    // The next keystroke must survive the write echoing back out of the URL.
    fireEvent.change(box, { target: { value: "alice" } });
    expect(box.value).toBe("alice");
    await waitFor(() => expect(currentUrl()).toBe("/users?q=alice"), {
      timeout: 2000,
    });
    expect(box.value).toBe("alice");
    expect(document.activeElement).toBe(box);
    // The URL write and the refetch it triggers land in separate ticks, so the
    // request lags the URL by an effect — asserting it synchronously here reads
    // the previous keystroke's query whenever the suite runs under load.
    await waitFor(() => expect(lastQuery()?.q).toBe("alice"), {
      timeout: 2000,
    });
  });

  it("清空筛选把 URL 收回裸 /users，连防抖窗口里的输入一起", async () => {
    vi.mocked(listUsers).mockResolvedValue(listResp([], 0));

    renderUsers("/users?role=admin&page=2");
    expect(await screen.findByText("没有匹配的用户")).toBeTruthy();

    const box = screen.getByLabelText("搜索用户名 / 昵称") as HTMLInputElement;
    fireEvent.change(box, { target: { value: "ghost" } });
    fireEvent.click(screen.getByRole("button", { name: "清空筛选" }));

    expect(box.value).toBe("");
    await waitFor(() => expect(currentUrl()).toBe("/users"));
    await waitFor(() => {
      expect(lastQuery()?.page).toBe(1);
      expect(lastQuery()?.q).toBe("");
      expect(lastQuery()?.role).toBeUndefined();
    });

    // "ghost" never reached the URL, so clearing the URL alone would leave its pending
    // write to land 300ms later and re-filter the roster behind the operator's back.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 400));
    });
    expect(currentUrl()).toBe("/users");
    expect(lastQuery()?.q).toBe("");
  });

  it("连切两次筛选：先发的旧响应后到也不覆盖新结果", async () => {
    const pending: {
      resolve: (v: AdminUserListResponse) => void;
      promise: Promise<AdminUserListResponse>;
    }[] = [];
    vi.mocked(listUsers).mockImplementation(() => {
      let resolve!: (v: AdminUserListResponse) => void;
      const promise = new Promise<AdminUserListResponse>((r) => {
        resolve = r;
      });
      pending.push({ resolve, promise });
      return promise;
    });

    renderUsers();
    await waitFor(() => expect(pending.length).toBe(1));
    pending[0]!.resolve(
      listResp([userItem({ id: "u0", display_name: "全部用户" })], 5),
    );
    expect(await screen.findByText("全部用户")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("按角色筛选"), {
      target: { value: "admin" },
    });
    await waitFor(() => expect(pending.length).toBe(2));
    // Filters stay live while a refresh is in flight — freezing them on every `loading`
    // would make this interleaving (and the guard below) unreachable.
    expect(
      (screen.getByLabelText("按状态筛选") as HTMLSelectElement).disabled,
    ).toBe(false);
    fireEvent.change(screen.getByLabelText("按状态筛选"), {
      target: { value: "disabled" },
    });
    await waitFor(() => expect(pending.length).toBe(3));

    pending[2]!.resolve(
      listResp([userItem({ id: "u2", display_name: "已停用管理员" })], 1),
    );
    expect(await screen.findByText("已停用管理员")).toBeTruthy();

    // Settle the superseded role-only request *inside* act so its continuation (and any
    // state it sets) is flushed before the assertions run.
    await act(async () => {
      pending[1]!.resolve(
        listResp([userItem({ id: "u1", display_name: "过期结果" })], 9),
      );
      await pending[1]!.promise;
    });

    expect(screen.getByText("已停用管理员")).toBeTruthy();
    expect(screen.queryByText("过期结果")).toBeNull();
    expect(screen.getByText(/共 1 个账号/)).toBeTruthy();
  });
});
