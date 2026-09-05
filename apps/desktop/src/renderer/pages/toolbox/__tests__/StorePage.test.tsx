import { StorePage } from "@/pages/toolbox/StorePage";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import type { SkillStoreListing } from "@/services/skillStore";
// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

vi.mock("@/services/skillStore", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/skillStore")>();
  return {
    ...actual,
    listSkillStore: vi.fn(),
    getSkillStoreListing: vi.fn(),
    installSkill: vi.fn(),
    reportSkill: vi.fn(),
  };
});

vi.mock("@/services/skillCatalog", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/skillCatalog")>();
  return {
    ...actual,
    replaceSkillSlot: vi.fn(),
    muteSkillSlot: vi.fn(),
    unmuteSkillSlot: vi.fn(),
    restoreSkillSlot: vi.fn(),
  };
});

const { listSkillStore, getSkillStoreListing, installSkill } = await import(
  "@/services/skillStore"
);
const { replaceSkillSlot, muteSkillSlot } = await import(
  "@/services/skillCatalog"
);

const ROW: SkillStoreListing = {
  id: "listing-1",
  name: "合同审查",
  description: "审合同时用",
  author: "ssauthor",
  version: "1",
  installed: false,
  hasUpdate: false,
  documentId: "doc-1",
  status: "published",
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[APP_PATHS.toolbox.store]}>
      <StorePage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(listSkillStore).mockReset();
  vi.mocked(getSkillStoreListing).mockReset();
  vi.mocked(installSkill).mockReset();
  vi.mocked(replaceSkillSlot).mockReset();
  vi.mocked(muteSkillSlot).mockReset();
  vi.mocked(listSkillStore).mockResolvedValue({
    items: [ROW],
    page: 1,
    pageSize: 24,
    total: 1,
  });
  vi.mocked(getSkillStoreListing).mockResolvedValue({
    ...ROW,
    content: "HOW 正文",
  });
  vi.mocked(installSkill).mockResolvedValue({
    ...ROW,
    installed: true,
    hasUpdate: false,
  });
});

afterEach(cleanup);

describe("商店页", () => {
  it("挂在工具箱子页，不带对话输入区", async () => {
    renderPage();
    await screen.findByRole("button", { name: "合同审查" });
    expect(
      screen.getByRole("heading", { level: 1, name: "商店" }),
    ).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "工具箱能力" })).toBeNull();
    expect(
      screen.getByRole("link", { name: "工具箱" }).getAttribute("href"),
    ).toBe(APP_PATHS.toolbox.root);
    expect(screen.queryByLabelText(/模型组合：/)).toBeNull();
    expect(screen.queryByLabelText(/权限：/)).toBeNull();
    expect(screen.queryByRole("textbox", { name: /给 Agent/ })).toBeNull();
  });

  it("点卡片出侧栏，安装只调 install，不换槽", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "合同审查" }));
    expect(await screen.findByTestId("skill-store-drawer")).toBeTruthy();
    expect(screen.getByTestId("skill-store-drawer").textContent).toContain(
      "审合同时用",
    );
    fireEvent.click(screen.getByRole("button", { name: "安装" }));
    await waitFor(() => {
      expect(installSkill).toHaveBeenCalledWith("listing-1");
    });
    expect(replaceSkillSlot).not.toHaveBeenCalled();
    expect(muteSkillSlot).not.toHaveBeenCalled();
  });

  it("已装同版本显示已装；有更新则点更新仍走同一 install", async () => {
    vi.mocked(listSkillStore).mockResolvedValue({
      items: [{ ...ROW, installed: true, hasUpdate: true }],
      page: 1,
      pageSize: 24,
      total: 1,
    });
    vi.mocked(getSkillStoreListing).mockResolvedValue({
      ...ROW,
      installed: true,
      hasUpdate: true,
      content: "HOW 正文",
    });
    vi.mocked(installSkill).mockResolvedValue({
      ...ROW,
      installed: true,
      hasUpdate: false,
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "合同审查" }));
    fireEvent.click(await screen.findByRole("button", { name: "更新" }));
    await waitFor(() => {
      expect(installSkill).toHaveBeenCalledWith("listing-1");
    });
    expect(replaceSkillSlot).not.toHaveBeenCalled();
  });
});
