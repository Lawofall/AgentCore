// @vitest-environment jsdom
/**
 * Regression tests for the admin 商店 page.
 *
 * Takedown leaves the public shelf but must not pretend to delete copies already
 * installed on user accounts — that used to be easy to over-claim in the confirm
 * copy. Pins the queue + listing status, the confirmation, and the POST.
 * The leading block comment keeps the @vitest-environment directive file-leading.
 */

import { SkillStorePage } from "@/pages/SkillStorePage";
import {
  type SkillStoreListing,
  type SkillStoreListingListResponse,
  type SkillStoreReport,
  type SkillStoreReportListResponse,
  listSkillStoreListings,
  listSkillStoreReports,
  getSkillStoreListing,
  takedownSkillStoreListing,
} from "@/services/adminSkillStore";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/adminSkillStore", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/adminSkillStore")>();
  return {
    ...actual,
    listSkillStoreListings: vi.fn(),
    listSkillStoreReports: vi.fn(),
    getSkillStoreListing: vi.fn(),
    takedownSkillStoreListing: vi.fn(),
  };
});
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function listing(
  p: Partial<SkillStoreListing> & { id: string; name: string },
): SkillStoreListing {
  return {
    author: "作者甲",
    author_user_id: "u-author",
    description: "技能说明",
    status: "published",
    updated_at: "2026-09-01T00:00:00Z",
    version_n: 1,
    ...p,
  };
}

function report(
  p: Partial<SkillStoreReport> & { id: string; listing_id: string },
): SkillStoreReport {
  return {
    created_at: "2026-09-02T00:00:00Z",
    listing_name: "合同审查",
    listing_status: "published",
    reason: "正文有诱导外链",
    reporter: "举报人乙",
    user_id: "u-reporter",
    ...p,
  };
}

function listingsResp(
  data: SkillStoreListing[],
  total = data.length,
): SkillStoreListingListResponse {
  return { data, total, page: 1, page_size: 50 };
}

function reportsResp(
  data: SkillStoreReport[],
  total = data.length,
): SkillStoreReportListResponse {
  return { data, total, page: 1, page_size: 50 };
}

function SearchProbe() {
  const [params] = useSearchParams();
  return <span data-testid="search">{params.toString()}</span>;
}

const search = () => screen.getByTestId("search").textContent;

function renderStore(initial = "/store") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <SearchProbe />
      <SkillStorePage />
    </MemoryRouter>,
  );
}

function mockRoster(
  listings: SkillStoreListing[],
  reports: SkillStoreReport[],
) {
  vi.mocked(listSkillStoreListings).mockResolvedValue(listingsResp(listings));
  vi.mocked(listSkillStoreReports).mockResolvedValue(reportsResp(reports));
}

describe("SkillStorePage", () => {
  it("渲染举报列表与 listing 状态", async () => {
    mockRoster(
      [listing({ id: "lst-1", name: "合同审查", status: "published" })],
      [
        report({
          id: "r1",
          listing_id: "lst-1",
          listing_name: "合同审查",
          listing_status: "published",
        }),
      ],
    );

    renderStore();

    expect(await screen.findByText("正文有诱导外链")).toBeTruthy();
    expect(screen.getByText(/不会删除用户已安装的副本/)).toBeTruthy();
    expect(screen.getByText("举报人乙")).toBeTruthy();
    const tables = screen.getAllByRole("table");
    expect(within(tables[0]!).getByText("已上架")).toBeTruthy();
    expect(within(tables[1]!).getByText("合同审查")).toBeTruthy();
    expect(within(tables[1]!).getByText("已上架")).toBeTruthy();
    expect(screen.getByText(/共 1 条/)).toBeTruthy();
  });

  it("下架要先确认，并说明不会删除已装副本", async () => {
    const row = listing({ id: "lst-1", name: "合同审查" });
    mockRoster(
      [row],
      [report({ id: "r1", listing_id: "lst-1", listing_name: "合同审查" })],
    );
    vi.mocked(takedownSkillStoreListing).mockResolvedValue({
      ...row,
      status: "taken_down",
    });

    renderStore();

    fireEvent.click((await screen.findAllByRole("button", { name: /下架/ }))[0]!);
    expect(vi.mocked(takedownSkillStoreListing)).not.toHaveBeenCalled();

    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText(/不会被删除/)).toBeTruthy();
    expect(dialog.queryByText(/删除用户已安装/)).toBeNull();
    fireEvent.click(dialog.getByRole("button", { name: /确认下架/ }));

    await waitFor(() =>
      expect(vi.mocked(takedownSkillStoreListing)).toHaveBeenCalledWith("lst-1"),
    );
  });

  it("listing 表的下架确认后同样走 takedown", async () => {
    const row = listing({ id: "lst-9", name: "数据分析 SOP" });
    mockRoster([row], []);
    vi.mocked(takedownSkillStoreListing).mockResolvedValue({
      ...row,
      status: "taken_down",
    });

    renderStore();

    const listingTable = within(
      await screen.findByRole("table"),
    );
    fireEvent.click(listingTable.getByRole("button", { name: /下架/ }));
    expect(vi.mocked(takedownSkillStoreListing)).not.toHaveBeenCalled();

    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: /确认下架/,
      }),
    );

    await waitFor(() =>
      expect(vi.mocked(takedownSkillStoreListing)).toHaveBeenCalledWith("lst-9"),
    );
  });

  it("带 status 的链接直接打开就复现筛选后的 listing 视图", async () => {
    mockRoster(
      [listing({ id: "lst-1", name: "合同审查", status: "taken_down" })],
      [],
    );

    renderStore("/store?status=taken_down");

    expect(await screen.findByText("合同审查")).toBeTruthy();
    expect(vi.mocked(listSkillStoreListings).mock.calls[0]?.[0]).toMatchObject({
      status: "taken_down",
      page: 1,
    });
    expect(
      (screen.getByLabelText("按 listing 状态筛选") as HTMLSelectElement).value,
    ).toBe("taken_down");
  });

  it("链接里的非法状态回落成「全部」，不原样转发给接口", async () => {
    mockRoster([listing({ id: "lst-1", name: "合同审查" })], []);

    renderStore("/store?status=banana");

    expect(await screen.findByText("合同审查")).toBeTruthy();
    expect(
      vi.mocked(listSkillStoreListings).mock.calls[0]?.[0]?.status,
    ).toBeUndefined();
    expect(
      (screen.getByLabelText("按 listing 状态筛选") as HTMLSelectElement).value,
    ).toBe("all");
  });

  it("改筛选写进 URL，并在同一次导航里丢掉 ?page=", async () => {
    mockRoster([listing({ id: "lst-1", name: "合同审查" })], []);

    renderStore("/store?page=3");
    await screen.findByText("合同审查");

    fireEvent.change(screen.getByLabelText("按 listing 状态筛选"), {
      target: { value: "published" },
    });

    await waitFor(() => expect(search()).toBe("status=published"));
    await waitFor(() =>
      expect(vi.mocked(listSkillStoreListings)).toHaveBeenCalledTimes(2),
    );
    expect(vi.mocked(listSkillStoreListings).mock.calls[1]?.[0]).toMatchObject({
      status: "published",
      page: 1,
    });
  });

  it("筛选后的空态说明是筛选结果，并能清除筛选", async () => {
    mockRoster([], []);
    renderStore();

    expect(await screen.findByText("还没有 listing")).toBeTruthy();
    expect(screen.getByText("还没有举报")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("按 listing 状态筛选"), {
      target: { value: "taken_down" },
    });
    expect(
      await screen.findByText("没有「平台已下架」状态的 listing"),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    await waitFor(() =>
      expect(
        (screen.getByLabelText("按 listing 状态筛选") as HTMLSelectElement)
          .value,
      ).toBe("all"),
    );
    expect(search()).toBe("");
  });

  it("已下架的 listing 不再提供下架按钮", async () => {
    mockRoster(
      [listing({ id: "lst-1", name: "合同审查", status: "taken_down" })],
      [
        report({
          id: "r1",
          listing_id: "lst-1",
          listing_name: "合同审查",
          listing_status: "taken_down",
        }),
      ],
    );

    renderStore();

    expect(await screen.findByText("正文有诱导外链")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /下架/ })).toBeNull();
    expect(screen.getAllByText("合同审查").length).toBeGreaterThan(0);
    expect(screen.getAllByText("平台已下架").length).toBeGreaterThan(0);
  });

  it("看正文打开当前版本快照", async () => {
    mockRoster(
      [listing({ id: "lst-1", name: "合同审查", status: "published" })],
      [],
    );
    vi.mocked(getSkillStoreListing).mockResolvedValue({
      ...listing({ id: "lst-1", name: "合同审查", status: "published" }),
      content: "怎么审合同",
    });

    renderStore();
    fireEvent.click(await screen.findByRole("button", { name: /看正文/ }));
    expect(await screen.findByText("怎么审合同")).toBeTruthy();
    expect(vi.mocked(getSkillStoreListing)).toHaveBeenCalledWith("lst-1");
  });
});
