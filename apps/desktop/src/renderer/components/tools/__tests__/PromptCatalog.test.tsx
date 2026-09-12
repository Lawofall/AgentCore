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

vi.mock("@/hooks/useLlmProviders", () => ({
  useLlmProviders: () => ({ data: undefined }),
}));

vi.mock("@/hooks/useModels", () => ({
  useModels: () => ({ data: undefined }),
}));

const { getSkillCatalog } = await import("@/services/skillCatalog");
const { listInstalledSkills } = await import("@/services/skillStore");
const { listMemoryUpdates } = await import("@/services/memory");
const {
  createRuleFolder,
  listAccountPromptTree,
  listScopeEntries,
  renameDocument,
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

function alwaysRail() {
  return screen.getByTestId("prompt-rail-always");
}

function onDemandRail() {
  return screen.getByTestId("prompt-rail-on-demand");
}

async function openReadDialog(from: HTMLElement, label: string) {
  fireEvent.click(within(from).getByRole("button", { name: label }));
  return screen.findByRole("dialog");
}

function previewText(from: HTMLElement, text: string) {
  return within(from).getByText(text, { exact: false });
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
  vi.mocked(renameDocument).mockReset();
  vi.mocked(listAccountPromptTree).mockReset();
  vi.mocked(listAccountPromptTree).mockResolvedValue({
    rulesDirId: "rules",
    folders: [],
    documents: [],
  });
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

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("PromptCatalog 最近学到", () => {
  it("整页概览，不套整页卡片，没有左栏目录", async () => {
    renderCatalog();
    await waitFor(() => {
      expect(screen.getByTestId("prompt-overview")).toBeTruthy();
    });
    const catalog = screen.getByTestId("prompt-catalog");
    expect(catalog.className).not.toContain("rounded-xl");
    expect(catalog.className).not.toContain("bg-card");
    expect(screen.queryByRole("navigation", { name: "提示词目录" })).toBeNull();
  });

  it("无 updates 时按加载档分两区", async () => {
    renderCatalog();
    await waitFor(() => {
      expect(screen.getByTestId("prompt-overview")).toBeTruthy();
    });
    expect(screen.queryByText("自带")).toBeNull();
    expect(screen.getByRole("heading", { name: "常驻" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "按需" })).toBeTruthy();
    expect(screen.getByText("每回合都带着")).toBeTruthy();
    expect(screen.getByText("用到才翻")).toBeTruthy();
    expect(screen.queryByText("记忆")).toBeNull();
    expect(
      within(onDemandRail()).getByRole("heading", { name: "官方" }),
    ).toBeTruthy();
    expect(previewText(onDemandRail(), "薄技能")).toBeTruthy();
    expect(within(alwaysRail()).queryByText("薄技能")).toBeNull();
    expect(
      within(screen.getByTestId("prompt-rail-memory")).getByText("偏好"),
    ).toBeTruthy();
    expect(screen.getByText("画像")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "概览" })).toBeNull();
    expect(screen.getByRole("button", { name: "新建条目" })).toBeTruthy();
    expect(screen.getByTestId("prompt-overview-updates").textContent).toMatch(
      /最近学到/,
    );
    const dialog = await openReadDialog(alwaysRail(), "角色身份");
    expect(
      within(dialog).getByRole("heading", { name: "角色身份" }),
    ).toBeTruthy();
    expect(within(dialog).getByText("官方")).toBeTruthy();
    expect(screen.queryByTestId("memory-updates-view")).toBeNull();
    expect(screen.getByTestId("prompt-overview")).toBeTruthy();
  });

  it("点两条官方 HOW 各自打开正文", async () => {
    renderCatalog("/toolbox/mine/skills", {
      ...base,
      skills: [
        {
          name: "run",
          summary: "跑命令 / 启服",
          body: "run-how-body",
          group: "工具",
        },
        {
          name: "staffing",
          summary: "团队拆法",
          body: "staffing-how-body",
          group: "编排",
        },
      ],
    });
    await waitFor(() => {
      expect(previewText(onDemandRail(), "团队拆法")).toBeTruthy();
    });
    const dialog = await openReadDialog(onDemandRail(), "跑命令 / 启服");
    const first = await screen.findByTestId("factory-skill-editor");
    expect(
      within(dialog).getByRole("heading", { name: "跑命令 / 启服" }),
    ).toBeTruthy();
    expect(within(dialog).getByText("官方")).toBeTruthy();
    expect(within(first).getByText("run-how-body")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "关闭" }));
    await openReadDialog(onDemandRail(), "团队拆法");
    const second = await screen.findByTestId("factory-skill-editor");
    expect(within(second).getByText("staffing-how-body")).toBeTruthy();
    expect(within(second).queryByText("run-how-body")).toBeNull();
  });

  it("官方 HOW 收进官方夹，不出五组夹名", async () => {
    renderCatalog("/toolbox/mine/skills", {
      ...base,
      skills: [
        { name: "run", summary: "跑命令 / 启服", body: "r", group: "工具" },
        { name: "staffing", summary: "团队拆法", body: "s", group: "编排" },
      ],
    });
    await waitFor(() => {
      expect(previewText(onDemandRail(), "团队拆法")).toBeTruthy();
    });
    expect(
      within(onDemandRail()).getByRole("heading", { name: "官方" }),
    ).toBeTruthy();
    expect(within(onDemandRail()).queryByText("编排")).toBeNull();
    expect(within(onDemandRail()).queryByText("工作区")).toBeNull();
    expect(within(onDemandRail()).queryByText("交付")).toBeNull();
    expect(within(onDemandRail()).queryByText("产品")).toBeNull();
    expect(previewText(onDemandRail(), "跑命令 / 启服")).toBeTruthy();
  });

  it("无官方 HOW 时不出现官方夹", async () => {
    renderCatalog("/toolbox/mine/skills", { ...base, skills: [] });
    await waitFor(() => {
      expect(screen.getByTestId("prompt-overview")).toBeTruthy();
    });
    expect(within(onDemandRail()).queryByText("官方")).toBeNull();
  });

  it("?updates=1 时弹窗是流水账，底下概览还在", async () => {
    renderCatalog("/toolbox/mine/skills?updates=1");
    expect(await screen.findByTestId("memory-updates-view")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText("还没有学到的内容")).toBeTruthy();
    });
    expect(screen.queryByRole("heading", { name: "角色身份" })).toBeNull();
    expect(screen.getAllByText("最近学到").length).toBeGreaterThan(0);
    expect(screen.getByTestId("prompt-overview")).toBeTruthy();
    expect(screen.getByTestId("prompt-rail-always")).toBeTruthy();
    expect(screen.getByTestId("prompt-rail-on-demand")).toBeTruthy();
    expect(
      within(screen.getByTestId("prompt-overview")).getByText("常驻"),
    ).toBeTruthy();
    expect(
      within(screen.getByTestId("prompt-overview")).getByText("按需"),
    ).toBeTruthy();
  });

  it("点最近学到开弹窗，关掉回到概览", async () => {
    renderCatalog();
    await waitFor(() => {
      expect(screen.getByTestId("prompt-overview")).toBeTruthy();
    });
    fireEvent.click(
      within(screen.getByTestId("prompt-overview-updates")).getByRole(
        "button",
        { name: "最近学到" },
      ),
    );
    expect(await screen.findByTestId("memory-updates-view")).toBeTruthy();
    expect(screen.getByTestId("prompt-overview")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByTestId("memory-updates-view")).toBeNull();
    expect(screen.getByTestId("prompt-overview")).toBeTruthy();
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
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "画像" })).toBeTruthy();
    expect(await screen.findByTestId("account-entry-editor")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "停用" })).toBeNull();
    expect(screen.queryByTestId("memory-updates-view")).toBeNull();
    expect(screen.queryByTestId("files-page")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    fireEvent.click(
      within(await screen.findByTestId("prompt-overview-updates")).getByRole(
        "button",
        { name: "最近学到" },
      ),
    );
    const again = await screen.findAllByTitle("在设定中打开画像");
    fireEvent.click(again[1] as HTMLElement);
    expect(await screen.findByTestId("files-page")).toBeTruthy();
  });

  it("自建条目点开后标题标我的", async () => {
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
    await waitFor(() => {
      expect(previewText(onDemandRail(), "合同审查")).toBeTruthy();
    });
    expect(within(onDemandRail()).getByText("其他")).toBeTruthy();
    expect(screen.queryByTestId("my-skills")).toBeTruthy();
    const dialog = await openReadDialog(onDemandRail(), "合同审查");
    expect(within(dialog).getByText("我的")).toBeTruthy();
    expect(await screen.findByTestId("mine-skill-editor")).toBeTruthy();
    expect(screen.getByLabelText("名称")).toHaveProperty("value", "合同审查");
    expect(screen.queryByRole("tablist", { name: "加载方式" })).toBeNull();
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
        group: "legal",
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
    await waitFor(() => {
      expect(previewText(onDemandRail(), "合同审查")).toBeTruthy();
    });
    const dialog = await openReadDialog(onDemandRail(), "合同审查");
    expect(within(dialog).getByText("市场")).toBeTruthy();
    expect(within(dialog).queryByText("我的")).toBeNull();
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
    await waitFor(() => {
      expect(previewText(onDemandRail(), "合同审查")).toBeTruthy();
    });
  }

  function startDrag(name: string, scope?: HTMLElement) {
    const dataTransfer = { setData: vi.fn(), effectAllowed: "" };
    const node = scope ? within(scope).getByText(name) : screen.getByText(name);
    fireEvent.dragStart(node, { dataTransfer });
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
    await renderMineCatalog();
    fireEvent.contextMenu(within(onDemandRail()).getByText("合同审查"));
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
    await renderMineCatalog();
    const tile = screen.getByTestId("prompt-tile-mine:d1");
    expect(tile.className).toContain("line-through");
    fireEvent.contextMenu(within(tile).getByText("合同审查"));
    expect(screen.queryByText("这条不对…")).toBeNull();
    fireEvent.click(await screen.findByText("恢复使用"));
    await waitFor(() => {
      expect(setDocumentDisputed).toHaveBeenCalledWith("d1", false);
    });
  });

  it("手写条目可拖", async () => {
    await renderMineCatalog();
    const started = startDrag("合同审查", onDemandRail());
    expect(started.setData).toHaveBeenCalledWith(
      PROMPT_DRAG_MIME,
      promptDragPayload({ kind: "mine", mineId: "d1" }),
    );
  });

  it("拖到常驻区里的条目也写成常驻", async () => {
    await renderMineCatalog();
    fireEvent.drop(screen.getByText("角色身份"), {
      dataTransfer: dropTransfer("d1"),
    });
    await waitFor(() => {
      expect(reparentDocument).toHaveBeenCalledWith("d1", "rules", "always");
    });
  });

  it("拖到常驻区头写成常驻", async () => {
    await renderMineCatalog();
    fireEvent.drop(alwaysRail(), {
      dataTransfer: dropTransfer("d1"),
    });
    await waitFor(() => {
      expect(reparentDocument).toHaveBeenCalledWith("d1", "rules", "always");
    });
  });

  it("拖到按需区头进其他", async () => {
    await renderMineCatalog();
    fireEvent.drop(onDemandRail(), {
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

  it("官方 HOW 拖不动", async () => {
    await renderMineCatalog();
    expect(
      startDrag("团队拆法", onDemandRail()).setData,
    ).not.toHaveBeenCalled();
  });

  it("拖进其他调 reparentDocument 不写官方 home", async () => {
    await renderMineCatalog();
    fireEvent.drop(within(onDemandRail()).getByText("其他"), {
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
    await renderMineCatalog();
    fireEvent.drop(screen.getByText("角色身份"), {
      dataTransfer: dropSkillTransfer("staffing"),
    });
    expect(reparentDocument).not.toHaveBeenCalled();
  });

  it("官方夹拒拖放", async () => {
    await renderMineCatalog();
    fireEvent.drop(screen.getByTestId("prompt-rail-official"), {
      dataTransfer: dropTransfer("d1"),
    });
    expect(reparentDocument).not.toHaveBeenCalled();
  });

  it("偏好仍拖不动", async () => {
    await renderMineCatalog();
    expect(startDrag("偏好").setData).not.toHaveBeenCalled();
  });
});

const webSearchTool = {
  name: "web_search",
  face: "web" as const,
  resident: true,
  summary: "联网检索",
  description: "联网检索：给出查询词。",
  parameters: {
    type: "object",
    properties: {
      query: { type: "string", description: "检索词，建议 2–3 个核心词。" },
    },
    required: ["query"],
  },
  approval: "never" as const,
  available_to: ["ceo", "worker"],
};

const hostTool = {
  name: "host",
  face: "host_browser" as const,
  resident: false,
  summary: "本机",
  description: "本机",
  parameters: { type: "object", properties: {} },
  approval: "grantable" as const,
  available_to: ["worker"],
};

function toolsRail() {
  return screen.getByTestId("prompt-rail-tools");
}

describe("PromptCatalog 工具与连接器", () => {
  it("出厂工具合成一份货架，按能力面分组；点开弹窗先出示说明书", async () => {
    renderCatalog("/toolbox/mine/skills", {
      ...base,
      tools: [webSearchTool, hostTool],
    });
    await waitFor(() => {
      expect(screen.getByTestId("prompt-rail-tools")).toBeTruthy();
    });
    const dialog = await openReadDialog(toolsRail(), "web_search");
    expect(screen.getByRole("heading", { name: "web_search" })).toBeTruthy();
    expect(within(alwaysRail()).queryByText("web_search")).toBeNull();
    expect(within(onDemandRail()).queryByText("web_search")).toBeNull();
    expect(within(toolsRail()).getByText("web_search")).toBeTruthy();
    expect(within(dialog).queryByRole("button", { name: "返回" })).toBeNull();
    expect(screen.getByTestId("tool-face-guide").textContent).toMatch(/全员/);
    expect(screen.getByText("query")).toBeTruthy();
    expect(screen.getByText("要填")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("tab", { name: "源码" }));
    const source = screen.getByTestId("tool-face-source").textContent ?? "";
    expect(source).toContain('"name": "web_search"');
    expect(source).toContain("query");
    fireEvent.click(within(dialog).getByRole("button", { name: "关闭" }));
    const hostDialog = await openReadDialog(toolsRail(), "host");
    expect(screen.getByRole("heading", { name: "host" })).toBeTruthy();
    expect(within(hostDialog).getByText("没有要填的参数")).toBeTruthy();
    expect(screen.getByTestId("prompt-rail-tools-web")).toBeTruthy();
    expect(screen.getByTestId("prompt-rail-tools-host_browser")).toBeTruthy();
    expect(screen.queryByTestId("prompt-rail-resident-tools")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-deferred-tools")).toBeNull();
    expect(screen.getByTestId("prompt-rail-official")).toBeTruthy();
  });

  it("?tool= 直达该工具，不先出夹名单", async () => {
    renderCatalog("/toolbox/mine/skills?tool=web_search", {
      ...base,
      tools: [webSearchTool, hostTool],
    });
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByRole("heading", { name: "web_search" }),
    ).toBeTruthy();
    expect(within(dialog).queryByRole("button", { name: "返回" })).toBeNull();
    expect(screen.getByTestId("tool-face-guide")).toBeTruthy();
  });

  it("?connectors=1 直达添加连接器，不先出夹名单", async () => {
    vi.stubGlobal("mcpApi", {
      runOp: vi.fn(),
      listServers: vi.fn(async () => ({ ok: true as const, servers: [] })),
      upsertServer: vi.fn(),
      removeServer: vi.fn(),
      setServerEnabled: vi.fn(),
      testServer: vi.fn(),
    });
    renderCatalog("/toolbox/mine/skills?connectors=1");
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByRole("heading", { name: "新建连接器" }),
    ).toBeTruthy();
    expect(within(dialog).queryByRole("button", { name: "返回" })).toBeNull();
  });

  it("无 mcpApi 时不出现连接器区", async () => {
    renderCatalog("/toolbox/mine/skills", {
      ...base,
      tools: [webSearchTool],
    });
    await waitFor(() => {
      expect(screen.getByTestId("prompt-overview")).toBeTruthy();
    });
    expect(screen.queryByTestId("prompt-rail-connectors")).toBeNull();
    expect(screen.queryByRole("button", { name: "添加连接器" })).toBeNull();
  });

  it("插头在工具块的连接器组，不把报出的动作再铺一层", async () => {
    const listServers = vi.fn(async () => ({
      ok: true as const,
      servers: [
        {
          id: "fs",
          name: "Filesystem",
          enabled: true,
          command: "npx",
          args: ["-y", "@modelcontextprotocol/server-filesystem"],
          runtimeStatus: "ready" as const,
        },
        {
          id: "gh",
          name: "GitHub",
          enabled: true,
          command: "npx",
          args: [],
          runtimeStatus: "failed" as const,
          runtimeError: "GITHUB_TOKEN 未配置",
        },
      ],
    }));
    vi.stubGlobal("mcpApi", {
      runOp: vi.fn(),
      listServers,
      upsertServer: vi.fn(),
      removeServer: vi.fn(),
      setServerEnabled: vi.fn(),
      testServer: vi.fn(),
    });
    renderCatalog("/toolbox/mine/skills", { ...base, tools: [webSearchTool] });
    expect(
      await screen.findByRole("button", { name: "Filesystem" }),
    ).toBeTruthy();
    const connectors = screen.getByTestId("prompt-rail-connectors");
    expect(screen.getByTestId("prompt-rail-tools").contains(connectors)).toBe(
      true,
    );
    expect(onDemandRail().contains(connectors)).toBe(false);
    expect(
      within(connectors).getByRole("button", { name: "Filesystem" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "添加连接器" })).toBeTruthy();
    expect(screen.queryByText("mcp_fs_read_file")).toBeNull();
    expect(within(connectors).getByText("GitHub")).toBeTruthy();
    expect(within(connectors).getByText("已握手")).toBeTruthy();
    expect(within(connectors).getByText("失败")).toBeTruthy();
    const dialog = await openReadDialog(connectors, "Filesystem");
    expect(screen.getByRole("heading", { name: "编辑连接器" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "测试握手" })).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "关闭" }));
    await openReadDialog(connectors, "GitHub");
    const badges = screen.getAllByText("失败");
    expect(badges.some((node) => node.className.includes("destructive"))).toBe(
      true,
    );
  });

  it("listServers 失败时诚实说明，不拆出厂工具货架", async () => {
    vi.stubGlobal("mcpApi", {
      runOp: vi.fn(),
      listServers: vi.fn(async () => ({
        ok: false as const,
        error: { kind: "io", detail: "读配置失败" },
      })),
      upsertServer: vi.fn(),
      removeServer: vi.fn(),
      setServerEnabled: vi.fn(),
      testServer: vi.fn(),
    });
    renderCatalog("/toolbox/mine/skills", { ...base, tools: [webSearchTool] });
    expect(await screen.findByText("读配置失败")).toBeTruthy();
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("读配置失败");
    expect(alert.className).toContain("text-muted-foreground");
    expect(alert.className).not.toContain("destructive");
    const dialog = await openReadDialog(toolsRail(), "web_search");
    expect(
      within(dialog).getByRole("heading", { name: "web_search" }),
    ).toBeTruthy();
  });

  it("点添加连接器打开新建表单", async () => {
    vi.stubGlobal("mcpApi", {
      runOp: vi.fn(),
      listServers: vi.fn(async () => ({ ok: true as const, servers: [] })),
      upsertServer: vi.fn(),
      removeServer: vi.fn(),
      setServerEnabled: vi.fn(),
      testServer: vi.fn(),
    });
    renderCatalog();
    fireEvent.click(await screen.findByRole("button", { name: "添加连接器" }));
    expect(screen.getByRole("heading", { name: "新建连接器" })).toBeTruthy();
  });
});

describe("PromptCatalog 就地命名", () => {
  function ruleFolder(id: string, name: string) {
    return {
      id,
      parentId: "rules",
      folderId: null,
      kind: "folder" as const,
      role: "rule" as const,
      aiMaintained: false,
      applyMode: "on_demand" as const,
      description: "",
      name,
      frontmatterError: null,
      alwaysChars: null,
      disputedAt: null,
    };
  }

  it("点新建夹出现输入行，不调 createRuleFolder", async () => {
    renderCatalog();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "新建夹" })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "新建夹" }));
    expect(screen.getByRole("textbox", { name: "夹名称" })).toBeTruthy();
    expect(createRuleFolder).not.toHaveBeenCalled();
  });

  it("输入夹名回车后创建并出现在按需区", async () => {
    const created = ruleFolder("f-law", "法律");
    vi.mocked(createRuleFolder).mockResolvedValue(created);
    renderCatalog();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "新建夹" })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "新建夹" }));
    vi.mocked(listAccountPromptTree).mockResolvedValue({
      rulesDirId: "rules",
      folders: [created],
      documents: [],
    });
    const input = screen.getByRole("textbox", { name: "夹名称" });
    fireEvent.change(input, { target: { value: "法律" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => {
      expect(createRuleFolder).toHaveBeenCalledWith("法律");
    });
    await waitFor(() => {
      expect(within(onDemandRail()).getByText("法律")).toBeTruthy();
    });
  });

  it("Esc 取消新建夹不调 API", async () => {
    renderCatalog();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "新建夹" })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "新建夹" }));
    const input = screen.getByRole("textbox", { name: "夹名称" });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("textbox", { name: "夹名称" })).toBeNull();
    expect(createRuleFolder).not.toHaveBeenCalled();
  });

  it("右键重命名就地改名", async () => {
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
    vi.mocked(renameDocument).mockResolvedValue({
      id: "d1",
      parentId: null,
      folderId: null,
      kind: "document",
      role: "rule",
      aiMaintained: false,
      applyMode: "on_demand",
      description: "审合同时用",
      name: "新合同.md",
      frontmatterError: null,
      alwaysChars: null,
      disputedAt: null,
    });
    renderCatalog();
    await waitFor(() => {
      expect(previewText(onDemandRail(), "合同审查")).toBeTruthy();
    });
    fireEvent.contextMenu(within(onDemandRail()).getByText("合同审查"));
    fireEvent.click(await screen.findByText("重命名"));
    const input = screen.getByRole("textbox", { name: "条目名称" });
    expect((input as HTMLInputElement).value).toBe("合同审查");
    fireEvent.change(input, { target: { value: "新合同" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => {
      expect(renameDocument).toHaveBeenCalledWith("d1", "新合同.md");
    });
  });
});
