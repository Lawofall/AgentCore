import { __resetCapabilitiesCacheForTests } from "@/components/tools/useCapabilities";
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
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GuidelinesPage } from "../GuidelinesPage";

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

vi.mock("@/services/capabilities", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/capabilities")>();
  return {
    ...actual,
    getCapabilities: vi.fn(),
  };
});

vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => [
    {
      id: "folder-1",
      name: "项目A",
      mode: "cloud",
      localRootId: null,
      localSubpath: null,
      relPath: "项目A",
    },
  ],
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

vi.mock("@/services/skillCatalog", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/skillCatalog")>();
  return {
    ...actual,
    getSkillCatalog: vi.fn(),
  };
});

vi.mock("@/pages/toolbox/useEditingAssembly", () => ({
  useEditingAssembly: () => ({ profile: null, pending: false }),
}));

vi.mock("@/services/skillStore", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/skillStore")>();
  return {
    ...actual,
    listMySkillListings: vi.fn(async () => []),
    listInstalledSkills: vi.fn(async () => []),
    publishSkill: vi.fn(),
    publishSkillVersion: vi.fn(),
    unpublishSkill: vi.fn(),
  };
});

const { getCapabilities } = await import("@/services/capabilities");
const { getSkillCatalog } = await import("@/services/skillCatalog");
const { listScopeEntries, createRuleDocument, createRuleFolder } = await import(
  "@/services/documents"
);

const base: Capabilities = {
  guidelines: {
    shared_base: "共享准则正文",
    worker_leaf: "叶子身份正文",
    worker_captain: "可再委派队员身份正文",
    ceo_addon: "主 Agent 身份正文",
    ceo: "完整 CEO 提示词",
  },
  skills: [
    {
      name: "delegate_playbook",
      summary: "派单进阶",
      body: "body",
      group: "编排",
      blurb: "",
    },
  ],
  tools: [],
};

beforeEach(() => {
  __resetCapabilitiesCacheForTests();
  vi.mocked(getCapabilities).mockReset();
  vi.mocked(getSkillCatalog).mockReset();
  vi.mocked(getSkillCatalog).mockResolvedValue({
    slots: [],
    mine: [],
    folderId: null,
    writable: true,
  });
  vi.mocked(listScopeEntries).mockReset();
  vi.mocked(listScopeEntries).mockResolvedValue([]);
  vi.mocked(createRuleDocument).mockReset();
  vi.mocked(createRuleFolder).mockReset();
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
});

afterEach(cleanup);

function renderPage(path = "/toolbox/mine/skills") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <GuidelinesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("GuidelinesPage 提示词阅读器", () => {
  it("默认打开概览，点目录才换正文", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(base);
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("prompt-overview")).toBeTruthy();
    });
    expect(screen.queryByText("共享准则正文")).toBeNull();

    expect(screen.queryByText("角色身份")).toBeNull();
    expect(screen.getByRole("button", { name: "全员共享准则" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "必带" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "按需" })).toBeNull();
  });

  it("官方 HOW 以货架卡出现，不露内部名", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(base);
    renderPage("/toolbox/official");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "派单进阶" })).toBeTruthy();
    });
    expect(screen.queryByText("delegate_playbook")).toBeNull();
  });

  it("货架没有角色身份卡，也不展览工种人格或 addon 正文", async () => {
    const contract =
      "【落盘文件】成品写入工作区；正文只报路径、怎么用、关键取舍。";
    vi.mocked(getCapabilities).mockResolvedValue({
      ...base,
      guidelines: {
        ...base.guidelines,
        worker_leaf: `<身份>\n叶子身份。\n</身份>\n\n${contract}`,
        worker_captain: `<身份>\n可再委派身份。\n</身份>\n\n${contract}`,
        ceo_addon:
          "<身份>\n主 Agent 核。\n</身份>\n\n<按需目录>\n- lead_subteam：子队拆法\n</按需目录>",
      },
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("prompt-overview")).toBeTruthy();
    });
    expect(screen.queryByText("角色身份")).toBeNull();
    expect(screen.queryByText("主 Agent 核。")).toBeNull();
    expect(screen.queryByText("lead_subteam")).toBeNull();
    expect(screen.queryByText("叶子身份。")).toBeNull();
    expect(screen.queryByText("可再委派身份。")).toBeNull();
    expect(screen.queryByText("CEO 专属提示词")).toBeNull();
    expect(screen.queryByText("队员身份（队长）")).toBeNull();
    expect(screen.queryByText("队员身份（叶子）")).toBeNull();
    expect(screen.queryByText("队员交付合同")).toBeNull();
    expect(screen.queryByText("本节点交付形态")).toBeNull();
    expect(screen.queryByText(contract)).toBeNull();
  });

  it("我的目录有手写条目，没有官方 HOW", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(base);
    vi.mocked(getSkillCatalog).mockResolvedValue({
      slots: [
        {
          name: "delegate_playbook",
          summary: "派单进阶",
        },
      ],
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
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "合同审查" })).toBeTruthy();
    });
    const factory = screen.getByTestId("prompt-rail-factory");
    const shelf = screen.getByTestId("prompt-rail-shelf");
    expect(shelf.contains(factory)).toBe(true);
    expect(
      within(factory).getByRole("button", { name: "派单进阶" }),
    ).toBeTruthy();
    expect(within(factory).getByText("官方")).toBeTruthy();
    expect(screen.queryByTestId("my-skills")).toBeNull();
    expect(shelf.querySelector("[data-prompt-folder]")).toBeNull();
  });

  it("官方 HOW 不在目录里改", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(base);
    renderPage("/toolbox/official");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "派单进阶" })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "派单进阶" }));
    const how = await screen.findByRole("dialog");
    expect(within(how).getByText("body")).toBeTruthy();
    expect(within(how).queryByRole("tab", { name: "编辑" })).toBeNull();
    expect(screen.queryByRole("button", { name: "换用" })).toBeNull();
  });

  it("没有范围选择器；官方栏打开准则读卡", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(base);
    renderPage("/toolbox/official");

    await waitFor(() => {
      expect(screen.getByTestId("prompt-overview")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("全员共享准则"));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByRole("heading", { name: "全员共享准则" }),
    ).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "提示词目录" })).toBeNull();
    expect(screen.queryByLabelText("技能目录范围")).toBeNull();
    expect(screen.queryByLabelText("提示词目录范围")).toBeNull();
    expect(screen.queryByText("常驻模板")).toBeNull();
    expect(screen.queryByText("按需注入")).toBeNull();
    expect(screen.queryByText("我的技能")).toBeNull();
    expect(screen.queryByText("自带")).toBeNull();
    expect(screen.queryByTestId("prompt-rail-official")).toBeNull();
    expect(within(dialog).getByText("官方")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByText("偏好")).toBeNull();
    expect(screen.queryByText("画像")).toBeNull();
    expect(screen.queryByText("AI 可能改")).toBeNull();
    expect(screen.queryByTestId("account-entry-editor")).toBeNull();
    expect(screen.getByRole("button", { name: "派单进阶" })).toBeTruthy();
    expect(screen.queryByRole("tablist", { name: "加载方式" })).toBeNull();
    expect(screen.queryByRole("button", { name: "上架" })).toBeNull();
  });

  it("新建默认按需", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(base);
    vi.mocked(createRuleDocument).mockResolvedValue({
      id: "new-1",
      parentId: null,
      folderId: null,
      kind: "document",
      role: "rule",
      aiMaintained: false,
      applyMode: "on_demand",
      description: "",
      name: "未命名提示词.md",
      frontmatterError: null,
      alwaysChars: null,
      disputedAt: null,
      content: "",
      version: "v1",
      quotaWarning: null,
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /^新建$/ }));
    await waitFor(() => {
      expect(createRuleFolder).toHaveBeenCalledWith("其他");
      expect(createRuleDocument).toHaveBeenCalledWith(
        "未命名提示词.md",
        null,
        expect.any(String),
        "on_demand",
        "other-1",
      );
    });
  });
});
