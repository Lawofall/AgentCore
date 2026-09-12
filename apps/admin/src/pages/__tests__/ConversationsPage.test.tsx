// @vitest-environment jsdom
/**
 * Regression tests for the admin 对话 page (会话名册).
 *
 * A slow request from an older filter must not overwrite fresher rows. Filters live
 * in the URL so a pasted link reproduces the view. The leading block comment keeps the
 * @vitest-environment directive file-leading.
 */

import { ConversationsPage } from "@/pages/ConversationsPage";
import {
  type AdminConversationListItem,
  type AdminConversationListResponse,
  listConversations,
  listTurns,
} from "@/services/adminConversations";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/adminConversations", () => ({
  listConversations: vi.fn(),
  listTurns: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

type Deferred<T> = { resolve: (value: T) => void; promise: Promise<T> };

function makeDeferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { resolve, promise };
}

function conv(
  p: Partial<AdminConversationListItem> & { id: string; title: string },
): AdminConversationListItem {
  return {
    archived: false,
    cost_total: 0,
    created_at: "2026-08-01T00:00:00Z",
    delegated_turns: 0,
    display_name: "Alice",
    errors: 0,
    messages: 3,
    turns: 1,
    updated_at: "2026-08-01T00:00:00Z",
    user_id: "u1",
    username: "alice",
    workers: 0,
    ...p,
  };
}

function convResp(
  data: AdminConversationListItem[],
  total = data.length,
): AdminConversationListResponse {
  return { data, total, page: 1, page_size: 20 };
}

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{`${loc.pathname}${loc.search}`}</div>;
}

// Mirrors App.tsx: every segment (valid or not) matches the one `:segment` route, so a
// redirect re-renders this same element instead of mounting a fresh component.
function renderPage(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <LocationProbe />
      <Routes>
        <Route path="/conversations" element={<ConversationsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ConversationsPage", () => {
  it("打开名册而不是白屏", async () => {
    vi.mocked(listConversations).mockResolvedValue(
      convResp([conv({ id: "c1", title: "首个会话" })]),
    );

    renderPage("/conversations");

    expect(await screen.findByText("首个会话")).toBeTruthy();
    expect(screen.getByTestId("loc").textContent).toBe("/conversations");
    expect(listTurns).not.toHaveBeenCalled();
  });

  it("会话段：后到的旧响应不覆盖新筛选结果", async () => {
    const pending: Deferred<AdminConversationListResponse>[] = [];
    vi.mocked(listConversations).mockImplementation(() => {
      const d = makeDeferred<AdminConversationListResponse>();
      pending.push(d);
      return d.promise;
    });

    renderPage("/conversations");
    await waitFor(() => expect(pending.length).toBe(1));
    pending[0]!.resolve(convResp([conv({ id: "c0", title: "未筛选结果" })]));
    expect(await screen.findByText("未筛选结果")).toBeTruthy();

    // Two filter flips while the first one is still in flight (nothing disables the
    // controls during loading) → responses can land out of order.
    const [hasErrors, hasDelegated] = screen.getAllByRole("combobox");
    fireEvent.change(hasErrors as HTMLSelectElement, { target: { value: "yes" } });
    await waitFor(() => expect(pending.length).toBe(2));
    fireEvent.change(hasDelegated as HTMLSelectElement, {
      target: { value: "yes" },
    });
    await waitFor(() => expect(pending.length).toBe(3));

    pending[2]!.resolve(convResp([conv({ id: "c2", title: "最新筛选结果" })]));
    expect(await screen.findByText("最新筛选结果")).toBeTruthy();

    // Settle the stale response *inside* act, so its continuation and any state it
    // sets are flushed before the assertions run. Wrapping these in `waitFor` instead
    // would make them vacuous: "过期的中间响应 is absent" already holds on the first
    // check — before the stale update could possibly have landed — so waitFor returns
    // immediately and the test passes even with the guard removed.
    await act(async () => {
      pending[1]!.resolve(convResp([conv({ id: "c1", title: "过期的中间响应" })]));
      await pending[1]!.promise;
    });

    expect(screen.getByText("最新筛选结果")).toBeTruthy();
    expect(screen.queryByText("过期的中间响应")).toBeNull();
  });

  it("首屏冻结筛选，落地后解冻（刷新期间仍可改筛选）", async () => {
    const pending: Deferred<AdminConversationListResponse>[] = [];
    vi.mocked(listConversations).mockImplementation(() => {
      const d = makeDeferred<AdminConversationListResponse>();
      pending.push(d);
      return d.promise;
    });

    renderPage("/conversations");
    await waitFor(() => expect(pending.length).toBe(1));

    const errorFilter = () =>
      screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    expect(errorFilter().disabled).toBe(true);

    pending[0]!.resolve(convResp([conv({ id: "c1", title: "首个会话" })]));
    expect(await screen.findByText("首个会话")).toBeTruthy();
    expect(errorFilter().disabled).toBe(false);

    // 刷新期间不得再冻结——竞态守卫存在的意义就是允许中途改筛选。
    fireEvent.change(errorFilter(), { target: { value: "yes" } });
    await waitFor(() => expect(pending.length).toBe(2));
    expect(errorFilter().disabled).toBe(false);
  });

  it("换筛选时保留旧数据，不整块塌成加载态", async () => {
    const pending: Deferred<AdminConversationListResponse>[] = [];
    vi.mocked(listConversations).mockImplementation(() => {
      const d = makeDeferred<AdminConversationListResponse>();
      pending.push(d);
      return d.promise;
    });

    renderPage("/conversations");
    await waitFor(() => expect(pending.length).toBe(1));
    pending[0]!.resolve(convResp([conv({ id: "c1", title: "上一批结果" })]));
    expect(await screen.findByText("上一批结果")).toBeTruthy();

    fireEvent.change(screen.getAllByRole("combobox")[0] as HTMLSelectElement, {
      target: { value: "yes" },
    });
    await waitFor(() => expect(pending.length).toBe(2));

    // 新响应还没到，旧行必须还在（曾经整块换成居中 spinner）。
    expect(screen.getByText("上一批结果")).toBeTruthy();

    pending[1]!.resolve(convResp([conv({ id: "c2", title: "新一批结果" })]));
    expect(await screen.findByText("新一批结果")).toBeTruthy();
    expect(screen.queryByText("上一批结果")).toBeNull();
  });

  it("会话段：带筛选参数的链接直接打开就复现该视图", async () => {
    vi.mocked(listConversations).mockResolvedValue(
      convResp([conv({ id: "c1", title: "命中筛选的会话" })], 100),
    );

    const link =
      "/conversations?q=alpha&has_errors=yes&has_delegated=no&include_deleted=0&since=2026-08-01&until=2026-08-02&sort=cost&order=asc&page=3";
    renderPage(link);
    await screen.findByText("命中筛选的会话");

    expect(vi.mocked(listConversations).mock.calls[0]?.[0]).toMatchObject({
      page: 3,
      q: "alpha",
      hasErrors: true,
      hasDelegated: false,
      includeDeleted: false,
      since: "2026-08-01T00:00:00.000Z",
      until: "2026-08-02T23:59:59.999Z",
      sort: "cost",
      order: "asc",
    });

    // Controls show the shared filter, not defaults with a filtered table under them.
    expect(
      (screen.getByLabelText("搜索会话标题") as HTMLInputElement).value,
    ).toBe("alpha");
    const [hasErrors, hasDelegated] = screen.getAllByRole(
      "combobox",
    ) as HTMLSelectElement[];
    expect(hasErrors?.value).toBe("yes");
    expect(hasDelegated?.value).toBe("no");
    expect(
      (screen.getByLabelText("更新起始日期") as HTMLInputElement).value,
    ).toBe("2026-08-01");
    expect((screen.getByLabelText("含已删除") as HTMLInputElement).checked).toBe(
      false,
    );

    // Reading the link must not rewrite it: a normalizing write would drop ?page= and
    // refetch, so the recipient would land on page 1 of the sender's filter.
    expect(screen.getByTestId("loc").textContent).toBe(link);
    expect(listConversations).toHaveBeenCalledTimes(1);
  });

  it("会话段：搜索防抖后写进 URL 并丢掉页码，输入框不失焦也不吞字", async () => {
    vi.mocked(listConversations).mockResolvedValue(
      convResp([conv({ id: "c1", title: "搜索结果" })], 100),
    );

    renderPage("/conversations?page=4");
    await screen.findByText("搜索结果");

    const input = screen.getByLabelText("搜索会话标题") as HTMLInputElement;
    input.focus();
    fireEvent.change(input, { target: { value: "al" } });
    fireEvent.change(input, { target: { value: "alph" } });
    fireEvent.change(input, { target: { value: "alpha" } });
    expect(input.value).toBe("alpha");

    await waitFor(
      () =>
        expect(screen.getByTestId("loc").textContent).toBe(
          "/conversations?q=alpha",
        ),
      { timeout: 2000 },
    );
    expect(document.activeElement).toBe(input);
    // Intermediate keystrokes never reached the API. Asserting "the URL still says
    // page=4" right after a keystroke would instead race the real 300ms timer, and
    // loses that race whenever the suite runs under load.
    const queried = vi.mocked(listConversations).mock.calls.map((c) => c[0].q);
    expect(queried).not.toContain("al");
    expect(queried).not.toContain("alph");
    expect(input.value).toBe("alpha");
    expect(vi.mocked(listConversations).mock.calls.at(-1)?.[0]).toMatchObject({
      q: "alpha",
      page: 1,
    });

    // Typing on top of the value the URL just took must not be clobbered by it.
    fireEvent.change(input, { target: { value: "alphabet" } });
    expect(input.value).toBe("alphabet");
    await waitFor(() =>
      expect(screen.getByTestId("loc").textContent).toBe(
        "/conversations?q=alphabet",
      ),
    );
    expect(input.value).toBe("alphabet");
  });

  it("会话段：打字还没落地就改别的筛选，两个条件都保住", async () => {
    vi.mocked(listConversations).mockResolvedValue(
      convResp([conv({ id: "c1", title: "搜索结果" })]),
    );

    renderPage("/conversations");
    await screen.findByText("搜索结果");

    const input = screen.getByLabelText("搜索会话标题") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "alpha" } });
    fireEvent.change(screen.getAllByRole("combobox")[0] as HTMLSelectElement, {
      target: { value: "yes" },
    });

    // The select's write must not wipe the half-typed search out of the box, and the
    // debounce that lands after it must merge into the query string rather than
    // replace it — the operator asked for both conditions.
    expect(input.value).toBe("alpha");
    await waitFor(() =>
      expect(vi.mocked(listConversations).mock.calls.at(-1)?.[0]).toMatchObject({
        q: "alpha",
        hasErrors: true,
      }),
    );
    const search = new URLSearchParams(
      screen.getByTestId("loc").textContent?.split("?")[1] ?? "",
    );
    expect(search.get("q")).toBe("alpha");
    expect(search.get("has_errors")).toBe("yes");
  });

  it("会话段：排序落进 URL，默认方向不写进链接", async () => {
    vi.mocked(listConversations).mockResolvedValue(
      convResp([conv({ id: "c1", title: "待排序的会话" })]),
    );

    renderPage("/conversations");
    await screen.findByText("待排序的会话");

    fireEvent.click(screen.getByRole("button", { name: "按成本排序" }));
    await waitFor(() =>
      expect(screen.getByTestId("loc").textContent).toBe(
        "/conversations?sort=cost",
      ),
    );
    expect(vi.mocked(listConversations).mock.calls.at(-1)?.[0]).toMatchObject({
      sort: "cost",
      order: "desc",
    });

    fireEvent.click(screen.getByRole("button", { name: "按成本排序" }));
    await waitFor(() =>
      expect(screen.getByTestId("loc").textContent).toBe(
        "/conversations?sort=cost&order=asc",
      ),
    );
    expect(vi.mocked(listConversations).mock.calls.at(-1)?.[0]).toMatchObject({
      sort: "cost",
      order: "asc",
    });
  });
});
