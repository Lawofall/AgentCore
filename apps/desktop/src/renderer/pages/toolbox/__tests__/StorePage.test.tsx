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
  within,
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

vi.mock("@/services/workflows", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/workflows")>();
  return {
    ...actual,
    listWorkflowTemplates: vi.fn(async () => []),
  };
});

vi.mock("@/services/workflowStore", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/workflowStore")>();
  return {
    ...actual,
    listWorkflowStore: vi.fn(),
    getWorkflowStoreListing: vi.fn(),
    installWorkflow: vi.fn(),
    reportWorkflow: vi.fn(),
  };
});

const { listSkillStore, getSkillStoreListing, installSkill } = await import(
  "@/services/skillStore"
);
const { listWorkflowTemplates } = await import("@/services/workflows");
const { listWorkflowStore, getWorkflowStoreListing, installWorkflow } =
  await import("@/services/workflowStore");

const ROW: SkillStoreListing = {
  id: "listing-1",
  name: "合同审查",
  description: "审合同时用",
  author: "ssauthor",
  version: "1",
  installed: false,
  hasUpdate: false,
  documentId: "doc-1",
  installDocumentId: null,
  status: "published",
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[APP_PATHS.toolbox.market]}>
      <StorePage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(listSkillStore).mockReset();
  vi.mocked(getSkillStoreListing).mockReset();
  vi.mocked(installSkill).mockReset();
  vi.mocked(listWorkflowTemplates).mockReset();
  vi.mocked(listWorkflowTemplates).mockResolvedValue([]);
  vi.mocked(listWorkflowStore).mockReset();
  vi.mocked(getWorkflowStoreListing).mockReset();
  vi.mocked(installWorkflow).mockReset();
  vi.mocked(listWorkflowStore).mockResolvedValue({
    items: [],
    page: 1,
    pageSize: 24,
    total: 0,
  });
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

describe("市场页", () => {
  it("货架种类是 chip，不是顶栏种类 tab", async () => {
    renderPage();
    await screen.findByRole("button", { name: "合同审查" });
    const chips = screen.getByRole("group", { name: "货架种类" });
    expect(within(chips).getByRole("button", { name: "提示词" })).toBeTruthy();
    expect(within(chips).getByRole("button", { name: "工作流" })).toBeTruthy();
    expect(within(chips).queryByRole("button", { name: "工具" })).toBeNull();
    expect(within(chips).queryByRole("button", { name: "MCP" })).toBeNull();
    expect(within(chips).queryByRole("button", { name: "创作" })).toBeNull();
    expect(screen.queryByRole("navigation", { name: "工具箱种类" })).toBeNull();
    expect(
      screen.queryByRole("heading", { level: 1, name: "商店" }),
    ).toBeNull();
    expect(screen.queryByLabelText(/模型组合：/)).toBeNull();
  });

  it("点卡片出侧栏，安装只调 install", async () => {
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
  });

  it("首页官方精选不在技能条再铺一遍", async () => {
    vi.mocked(listSkillStore).mockResolvedValue({
      items: [
        {
          ...ROW,
          id: "listing-legal",
          name: "legal_answer_brief",
          description: "民事答辩状",
          author: "官方",
        },
      ],
      page: 1,
      pageSize: 24,
      total: 1,
    });
    renderPage();
    expect(
      await screen.findByRole("button", { name: "民事答辩状" }),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "官方精选" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "提示词" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "民事答辩状" })).toHaveLength(
      1,
    );
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
  });

  it("空货架只留标题，不写上架说明书", async () => {
    vi.mocked(listSkillStore).mockResolvedValue({
      items: [],
      page: 1,
      pageSize: 24,
      total: 0,
    });
    renderPage();
    expect(await screen.findByText("还没有可安装的内容")).toBeTruthy();
    expect(screen.queryByText(/上架/)).toBeNull();
    expect(screen.queryByText(/一键安装/)).toBeNull();
  });

  it("工作流 chip：官方模板走使用，用户 listing 走安装", async () => {
    vi.mocked(listSkillStore).mockResolvedValue({
      items: [],
      page: 1,
      pageSize: 24,
      total: 0,
    });
    vi.mocked(listWorkflowTemplates).mockResolvedValue([
      {
        id: "map_fanout",
        title: "多角度调研",
        summary: "分路调研再汇总",
        slots: [],
      },
    ]);
    vi.mocked(listWorkflowStore).mockResolvedValue({
      items: [
        {
          id: "wf-listing-1",
          name: "周报流水线",
          description: "每周写周报",
          author: "wfauthor",
          version: "1",
          installed: false,
          hasUpdate: false,
          workflowId: "wf-1",
          installWorkflowId: null,
          status: "published",
        },
      ],
      page: 1,
      pageSize: 24,
      total: 1,
    });
    vi.mocked(getWorkflowStoreListing).mockResolvedValue({
      id: "wf-listing-1",
      name: "周报流水线",
      description: "每周写周报",
      author: "wfauthor",
      version: "1",
      installed: false,
      hasUpdate: false,
      workflowId: "wf-1",
      installWorkflowId: null,
      status: "published",
      definition: { nodes: [], edges: [] },
    });
    vi.mocked(installWorkflow).mockResolvedValue({
      id: "wf-listing-1",
      name: "周报流水线",
      description: "每周写周报",
      author: "wfauthor",
      version: "1",
      installed: true,
      hasUpdate: false,
      workflowId: "wf-1",
      installWorkflowId: "copy-1",
      status: "published",
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "工作流" }));

    fireEvent.click(await screen.findByRole("button", { name: "多角度调研" }));
    expect(await screen.findByText(/使用 · 多角度调研/)).toBeTruthy();
    expect(screen.queryByTestId("workflow-store-drawer")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    fireEvent.click(await screen.findByRole("button", { name: "周报流水线" }));
    expect(await screen.findByTestId("workflow-store-drawer")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "安装" }));
    await waitFor(() => {
      expect(installWorkflow).toHaveBeenCalledWith("wf-listing-1");
    });
    expect(installSkill).not.toHaveBeenCalled();
  });
});
