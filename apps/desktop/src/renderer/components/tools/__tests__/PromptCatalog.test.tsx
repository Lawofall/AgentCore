import {
  PROMPT_DRAG_MIME,
  PROMPT_SKILL_DRAG_MIME,
  promptDragPayload,
} from "@/lib/promptCatalogDrag";
import type { Capabilities } from "@/services/capabilities";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PromptCatalog } from "../PromptCatalog";

vi.mock("@/components/markdown/MarkdownSourceEditor", async () => {
  const React = await import("react");
  let current = "";
  return {
    MarkdownSourceEditor: React.forwardRef(function Stub(
      props: {
        initialDoc?: string;
        onChange?: (value: string) => void;
      },
      ref: React.Ref<unknown>,
    ) {
      if (props.initialDoc != null) current = props.initialDoc;
      React.useImperativeHandle(ref, () => ({
        getValue: () => current,
        getView: () => null,
        getSelectionContext: () => null,
        startRewriteReview: () => false,
        endRewriteReview: () => undefined,
      }));
      return React.createElement("textarea", {
        "aria-label": "正文",
        defaultValue: props.initialDoc,
        onChange: (event: { target: { value: string } }) => {
          current = event.target.value;
          props.onChange?.(event.target.value);
        },
      });
    }),
  };
});
vi.mock("@/components/markdown/sourceToolbar", () => ({
  SourceToolbar: () => null,
}));

vi.mock("@/services/documents", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/documents")>();
  return {
    ...actual,
    listScopeEntries: vi.fn(async () => []),
    listAccountPromptTree: vi.fn(async () => ({
      rulesDirId: "rules",
      folders: [],
      documents: [],
    })),
    createRuleDocument: vi.fn(),
    createRuleFolder: vi.fn(),
    reparentDocument: vi.fn(),
    deleteDocument: vi.fn(),
    getDocument: vi.fn(),
    renameDocument: vi.fn(),
    writeDocument: vi.fn(),
    setDocumentDisputed: vi.fn(),
    updateDocumentApplyMode: vi.fn(),
  };
});

vi.mock("@/services/memory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/memory")>();
  return {
    ...actual,
    getMemoryFile: vi.fn(async () => ({ content: "画像正文", version: "v0" })),
    writeMemoryFile: vi.fn(),
    listMemoryUpdates: vi.fn(async () => []),
    listDisputedMemoryLines: vi.fn(async () => ({
      lines: [],
      maxPerEntry: 50,
    })),
  };
});

vi.mock("@/services/skillCatalog", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/skillCatalog")>();
  return {
    ...actual,
    getSkillCatalog: vi.fn(),
  };
});

vi.mock("@/services/skillStore", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/skillStore")>();
  return {
    ...actual,
    listMySkillListings: vi.fn(async () => []),
    listInstalledSkills: vi.fn(async () => []),
  };
});

vi.mock("@/hooks/useFolders", () => ({
  getFolders: () => [{ id: "F99", name: "白板" }],
  useFolders: () => [{ id: "F99", name: "白板" }],
}));

const { getSkillCatalog } = await import("@/services/skillCatalog");
const { listInstalledSkills } = await import("@/services/skillStore");
const { listMemoryUpdates } = await import("@/services/memory");
const {
  createRuleFolder,
  listScopeEntries,
  reparentDocument,
  setDocumentDisputed,
  updateDocumentApplyMode,
} = await import("@/services/documents");

const base: Capabilities = {
  guidelines: {
    shared_base: "共享准则正文",
    worker_leaf: "叶子身份正文",
    worker_captain: "可再委派队员身份正文",
    ceo_addon: "主 Agent 身份正文",
    ceo: "完整 CEO 提示词",
  },
  skills: [
    { name: "thin_skill", summary: "薄技能", body: "thin-body", group: "" },
  ],
  tools: [],
};

function renderCatalog(
  path = "/toolbox/mine/skills",
  data: Capabilities = base,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/toolbox/mine/skills"
            element={<PromptCatalog data={data} />}
          />
          <Route path="/files" element={<div data-testid="files-page" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(getSkillCatalog).mockReset();
  vi.mocked(getSkillCatalog).mockResolvedValue({
    slots: [],
    mine: [],
    folderId: null,
    writable: true,
  });
  vi.mocked(listInstalledSkills).mockReset();
  vi.mocked(listInstalledSkills).mockResolvedValue([]);
  vi.mocked(listMemoryUpdates).mockReset();
  vi.mocked(listMemoryUpdates).mockResolvedValue([]);
  vi.mocked(listScopeEntries).mockReset();
  vi.mocked(listScopeEntries).mockResolvedValue([]);
  vi.mocked(setDocumentDisputed).mockReset();
  vi.mocked(createRuleFolder).mockReset();
  vi.mocked(reparentDocument).mockReset();
  vi.mocked(createRuleFolder).mockResolvedValue({
    id: "other-1",
    parentId: "rules",
    folderId: null,
    kind: "folder",
    role: "rule",
    aiMaintained: false,
    applyMode: "on_demand",
    description: "",
    name: "其他",
    frontmatterError: null,
    alwaysChars: null,
    disputedAt: null,
  });
  vi.mocked(updateDocumentApplyMode).mockReset();
  vi.mocked(updateDocumentApplyMode).mockResolvedValue({
    id: "d1",
    parentId: null,
    folderId: null,
    kind: "document",
    role: "rule",
    aiMaintained: false,
    applyMode: "always",
    description: "审合同时用",
    name: "合同审查.md",
    frontmatterError: null,
    alwaysChars: null,
    disputedAt: null,
  });
});

afterEach(cleanup);

describe("PromptCatalog 最近更新", () => {
  it("目录与正文贴边分栏，不套整页卡片", async () => {
    renderCatalog();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "角色身份" })).toBeTruthy();
    });
    const catalog = screen.getByTestId("prompt-catalog");
    expect(catalog.className).not.toContain("rounded-xl");
    expect(catalog.className).not.toContain("bg-card");
    const nav = screen.getByRole("navigation", { name: "提示词目录" });
    expect(nav.className).toContain("border-r");
    expect(nav.className).toContain("bg-muted/30");
  });

  it("无 updates 时按加载档分两区，来源只在标题徽标", async () => {
    renderCatalog();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "角色身份" })).toBeTruthy();
    });
    const nav = screen.getByRole("navigation", { name: "提示词目录" });
    expect(within(nav).queryByText("自带")).toBeNull();
    expect(within(nav).queryByText("官方")).toBeNull();
    expect(within(nav).queryByText("我的")).toBeNull();
    expect(within(nav).queryByText("其他")).toBeNull();
    expect(within(nav).getByRole("group", { name: "常驻" })).toBeTruthy();
    expect(within(nav).getByRole("group", { name: "按需" })).toBeTruthy();
    expect(within(nav).queryByRole("group", { name: "记忆" })).toBeNull();
    expect(within(nav).getByText("薄技能")).toBeTruthy();
    expect(
      within(screen.getByTestId("prompt-rail-on-demand")).getByText("薄技能"),
    ).toBeTruthy();
    expect(
      within(screen.getByTestId("prompt-rail-always")).queryByText("薄技能"),
    ).toBeNull();
    expect(within(nav).queryByRole("separator")).toBeNull();
    expect(
      within(screen.getByTestId("prompt-rail-memory")).getByText("偏好"),
    ).toBeTruthy();
    expect(within(nav).getByText("画像")).toBeTruthy();
    expect(within(nav).getByRole("button", { name: "最近更新" })).toBeTruthy();
    expect(within(nav).getByRole("button", { name: "新建条目" })).toBeTruthy();
    expect(screen.getByText("官方")).toBeTruthy();
    expect(screen.queryByTestId("memory-updates-view")).toBeNull();
    expect(screen.queryByText("记忆动态")).toBeNull();
  });

  it("官方 HOW 平铺，不出分组夹名", async () => {
    renderCatalog("/toolbox/mine/skills", {
      ...base,
      skills: [
        { name: "run", summary: "跑命令 / 启服", body: "r", group: "工具" },
        { name: "staffing", summary: "团队拆法", body: "s", group: "编排" },
      ],
    });
    const nav = screen.getByRole("navigation", { name: "提示词目录" });
    await waitFor(() => {
      expect(within(nav).getByText("团队拆法")).toBeTruthy();
    });
    expect(within(nav).queryByText("编排")).toBeNull();
    expect(within(nav).queryByText("工具")).toBeNull();
    expect(within(nav).getByText("跑命令 / 启服")).toBeTruthy();
  });

  it("?updates=1 时右边是流水账，不是第三组目录", async () => {
    renderCatalog("/toolbox/mine/skills?updates=1");
    expect(await screen.findByTestId("memory-updates-view")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText("还没有记忆更新")).toBeTruthy();
    });
    expect(screen.queryByRole("heading", { name: "角色身份" })).toBeNull();
    expect(screen.queryByText("记忆动态")).toBeNull();
    const nav = screen.getByRole("navigation", { name: "提示词目录" });
    expect(within(nav).queryByText("自带")).toBeNull();
    expect(within(nav).getByRole("group", { name: "常驻" })).toBeTruthy();
    expect(within(nav).getByRole("group", { name: "按需" })).toBeTruthy();
    expect(within(nav).queryByText("其他")).toBeNull();
    expect(within(nav).queryByText("记忆动态")).toBeNull();
  });

  it("点最近更新把右边换成流水账", async () => {
    renderCatalog();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "角色身份" })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "最近更新" }));
    expect(await screen.findByTestId("memory-updates-view")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "角色身份" })).toBeNull();
  });

  it("点账里账号画像停在本页我的条目；文件夹的去文件页", async () => {
    vi.mocked(listMemoryUpdates).mockResolvedValue([
      {
        id: "u1",
        conversationId: "c1",
        createdAt: "2026-09-01T12:00:00Z",
        kind: "semantic",
        summary: null,
        items: [
          {
            action: "add",
            file: "画像",
            section: "关于用户的事实",
            scope: "global",
            content: "倾向使用 bun",
            target: "global/profile",
          },
          {
            action: "add",
            file: "画像",
            section: "技术栈与工具",
            scope: "project",
            content: "本项目用 Vite",
            target: "project/F99/profile",
            projectId: "F99",
          },
        ],
      },
    ]);
    renderCatalog("/toolbox/mine/skills?updates=1");
    const rows = await screen.findAllByTitle("在设定中打开画像");
    fireEvent.click(rows[0] as HTMLElement);
    expect(await screen.findByTestId("account-entry-editor")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "画像" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "停用" })).toBeNull();
    expect(screen.queryByTestId("memory-updates-view")).toBeNull();
    expect(screen.queryByTestId("files-page")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "最近更新" }));
    const again = await screen.findAllByTitle("在设定中打开画像");
    fireEvent.click(again[1] as HTMLElement);
    expect(await screen.findByTestId("files-page")).toBeTruthy();
  });

  it("自建条目用分割线与官方怎么做隔开，标题标我的", async () => {
    vi.mocked(getSkillCatalog).mockResolvedValue({
      slots: [],
      mine: [
        {
          id: "d1",
          name: "合同审查",
          description: "审合同时用",
          content: "HOW",
          version: "v1",
        },
      ],
      folderId: null,
      writable: true,
    });
    renderCatalog();
    const nav = screen.getByRole("navigation", { name: "提示词目录" });
    await waitFor(() => {
      expect(within(nav).getByText("合同审查")).toBeTruthy();
    });
    expect(within(nav).getByText("其他")).toBeTruthy();
    expect(within(nav).queryByRole("separator")).toBeNull();
    expect(screen.queryByTestId("my-skills")).toBeTruthy();
    fireEvent.click(within(nav).getByText("合同审查"));
    expect(await screen.findByTestId("mine-skill-editor")).toBeTruthy();
    expect(screen.getByText("我的")).toBeTruthy();
    expect(within(nav).queryByText("我的")).toBeNull();
    expect(screen.queryByRole("tablist", { name: "加载方式" })).toBeNull();
    expect(within(nav).getByRole("group", { name: "按需" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "停用" })).toBeNull();
  });

  it("市场装来的标题标市场，不标我的", async () => {
    vi.mocked(listInstalledSkills).mockResolvedValue([
      {
        id: "listing-1",
        name: "合同审查",
        description: "审合同时用",
        author: "官方",
        version: "1",
        installed: true,
        hasUpdate: false,
        documentId: null,
        installDocumentId: "d1",
        status: "published",
      },
    ]);
    vi.mocked(getSkillCatalog).mockResolvedValue({
      slots: [],
      mine: [
        {
          id: "d1",
          name: "合同审查",
          description: "审合同时用",
          content: "HOW",
          version: "v1",
        },
      ],
      folderId: null,
      writable: true,
    });
    renderCatalog();
    const nav = screen.getByRole("navigation", { name: "提示词目录" });
    await waitFor(() => {
      expect(within(nav).getByText("合同审查")).toBeTruthy();
    });
    fireEvent.click(within(nav).getByText("合同审查"));
    expect(await screen.findByTestId("mine-skill-editor")).toBeTruthy();
    expect(screen.getByText("市场")).toBeTruthy();
    expect(screen.queryByText("我的")).toBeNull();
    expect(screen.queryByRole("button", { name: "上架" })).toBeNull();
  });
});

describe("PromptCatalog 拖拽搬家", () => {
  async function renderMineCatalog() {
    vi.mocked(getSkillCatalog).mockResolvedValue({
      slots: [],
      mine: [
        {
          id: "d1",
          name: "合同审查",
          description: "审合同时用",
          content: "HOW",
          version: "v1",
        },
      ],
      folderId: null,
      writable: true,
    });
    renderCatalog("/toolbox/mine/skills", {
      ...base,
      skills: [
        { name: "staffing", summary: "团队拆法", body: "s", group: "编排" },
      ],
    });
    const nav = screen.getByRole("navigation", { name: "提示词目录" });
    await waitFor(() => {
      expect(within(nav).getByText("合同审查")).toBeTruthy();
    });
    return nav;
  }

  function startDrag(name: string) {
    const dataTransfer = { setData: vi.fn(), effectAllowed: "" };
    fireEvent.dragStart(screen.getByText(name), { dataTransfer });
    return dataTransfer;
  }

  function dropTransfer(mineId: string) {
    const raw = promptDragPayload({ kind: "mine", mineId });
    return {
      types: [PROMPT_DRAG_MIME],
      getData: (type: string) => (type === PROMPT_DRAG_MIME ? raw : ""),
    };
  }

  function dropSkillTransfer(slot: string) {
    const raw = promptDragPayload({ kind: "skill", slot });
    return {
      types: [PROMPT_SKILL_DRAG_MIME],
      dropEffect: "",
      getData: (type: string) => (type === PROMPT_SKILL_DRAG_MIME ? raw : ""),
    };
  }

  it("手写条目右键可删除，没有这条不对", async () => {
    const nav = await renderMineCatalog();
    fireEvent.contextMenu(within(nav).getByText("合同审查"));
    expect(await screen.findByText("重命名")).toBeTruthy();
    expect(screen.getByText("删除")).toBeTruthy();
    expect(screen.queryByText("移到根目录")).toBeNull();
    expect(screen.queryByText("移入编排")).toBeNull();
    expect(screen.queryByText("这条不对…")).toBeNull();
    expect(screen.queryByText("恢复使用")).toBeNull();
  });

  it("已 disputed 的手写条目仍划掉，右键恢复使用", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      {
        id: "d1",
        parentId: null,
        folderId: null,
        kind: "document",
        role: "rule",
        aiMaintained: false,
        applyMode: "on_demand",
        description: "审合同时用",
        name: "合同审查.md",
        frontmatterError: null,
        alwaysChars: null,
        disputedAt: "2026-07-19T12:00:00Z",
      },
    ]);
    const nav = await renderMineCatalog();
    const label = within(nav).getByText("合同审查");
    expect(label.className).toContain("line-through");
    fireEvent.contextMenu(label);
    expect(screen.queryByText("这条不对…")).toBeNull();
    fireEvent.click(await screen.findByText("恢复使用"));
    await waitFor(() => {
      expect(setDocumentDisputed).toHaveBeenCalledWith("d1", false);
    });
  });

  it("手写条目可拖", async () => {
    await renderMineCatalog();
    const started = startDrag("合同审查");
    expect(started.setData).toHaveBeenCalledWith(
      PROMPT_DRAG_MIME,
      promptDragPayload({ kind: "mine", mineId: "d1" }),
    );
  });

  it("拖到常驻区里的条目也写成常驻", async () => {
    const nav = await renderMineCatalog();
    fireEvent.drop(within(nav).getByText("角色身份"), {
      dataTransfer: dropTransfer("d1"),
    });
    await waitFor(() => {
      expect(reparentDocument).toHaveBeenCalledWith("d1", "rules", "always");
    });
  });

  it("拖到常驻区头写成常驻", async () => {
    const nav = await renderMineCatalog();
    fireEvent.drop(within(nav).getByRole("group", { name: "常驻" }), {
      dataTransfer: dropTransfer("d1"),
    });
    await waitFor(() => {
      expect(reparentDocument).toHaveBeenCalledWith("d1", "rules", "always");
    });
  });

  it("拖到按需区头进其他", async () => {
    const nav = await renderMineCatalog();
    fireEvent.drop(within(nav).getByRole("group", { name: "按需" }), {
      dataTransfer: dropTransfer("d1"),
    });
    await waitFor(() => {
      expect(createRuleFolder).toHaveBeenCalledWith("其他");
      expect(reparentDocument).toHaveBeenCalledWith(
        "d1",
        "other-1",
        "on_demand",
      );
    });
  });

  it("落到最近更新不搬家", async () => {
    await renderMineCatalog();
    fireEvent.drop(screen.getByText("最近更新"), {
      dataTransfer: dropTransfer("d1"),
    });
    expect(reparentDocument).not.toHaveBeenCalled();
  });

  it("官方 HOW 拖不动", async () => {
    await renderMineCatalog();
    expect(startDrag("团队拆法").setData).not.toHaveBeenCalled();
  });

  it("拖进其他调 reparentDocument 不写官方 home", async () => {
    const nav = await renderMineCatalog();
    fireEvent.drop(within(nav).getByText("其他"), {
      dataTransfer: dropTransfer("d1"),
    });
    await waitFor(() => {
      expect(createRuleFolder).toHaveBeenCalledWith("其他");
      expect(reparentDocument).toHaveBeenCalledWith(
        "d1",
        "other-1",
        "on_demand",
      );
    });
  });

  it("官方 HOW 拖到根不搬家", async () => {
    const nav = await renderMineCatalog();
    fireEvent.drop(within(nav).getByText("角色身份"), {
      dataTransfer: dropSkillTransfer("staffing"),
    });
    expect(reparentDocument).not.toHaveBeenCalled();
  });

  it("偏好仍拖不动", async () => {
    await renderMineCatalog();
    expect(startDrag("偏好").setData).not.toHaveBeenCalled();
  });
});
