import { __resetCapabilitiesCacheForTests } from "@/components/tools/useCapabilities";
import type { Capabilities } from "@/services/capabilities";
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

vi.mock("@/services/memory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/memory")>();
  return {
    ...actual,
    getMemoryFile: vi.fn(async () => ({ content: "", version: "v0" })),
    writeMemoryFile: vi.fn(),
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

function renderPage() {
  return render(
    <MemoryRouter>
      <GuidelinesPage />
    </MemoryRouter>,
  );
}

describe("GuidelinesPage 提示词阅读器", () => {
  it("默认打开角色身份 · 主 Agent，点目录才换正文", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(base);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("主 Agent 身份正文")).toBeTruthy();
    });
    expect(screen.queryByText("共享准则正文")).toBeNull();

    fireEvent.click(screen.getByText("全员共享准则"));
    expect(screen.getByText("共享准则正文")).toBeTruthy();
    expect(screen.queryByText("主 Agent 身份正文")).toBeNull();
    expect(screen.queryByText(/共享的基座/)).toBeNull();
  });

  it("薄技能目录和详情都用人话，不露出内部名", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(base);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("派单进阶")).toBeTruthy();
    });
    expect(screen.queryByText("delegate_playbook")).toBeNull();

    fireEvent.click(screen.getByText("派单进阶"));
    expect(screen.queryByText("delegate_playbook")).toBeNull();
    expect(screen.getByText("body")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "编辑" })).toBeNull();
    expect(screen.queryByRole("button", { name: "换用" })).toBeNull();
    expect(screen.queryByText(/出厂只读/)).toBeNull();
    expect(screen.queryByText(/出厂正文只读/)).toBeNull();
    expect(screen.queryByText(/不是独立能力/)).toBeNull();
    expect(screen.queryByText(/先在「我的技能」里写一份，再来换用/)).toBeNull();
  });

  it("渲染三选一角色身份，不把身份叠成四层", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(base);
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "角色身份" })).toBeTruthy();
    });
    expect(screen.getByRole("tab", { name: "主 Agent" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "可再委派的队员" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "叶子队员" })).toBeTruthy();
    expect(screen.getByText("主 Agent 身份正文")).toBeTruthy();
    expect(screen.queryByText(/本回合三选一/)).toBeNull();
    expect(screen.queryByText("CEO 专属提示词")).toBeNull();
    expect(screen.queryByText("队员身份（队长）")).toBeNull();
    expect(screen.queryByText("队员身份（叶子）")).toBeNull();
    expect(screen.queryByText("队员交付合同")).toBeNull();
    expect(screen.queryByText("本节点交付形态")).toBeNull();
    expect(screen.queryByText("用户只跟你说话，对整段对话负责。")).toBeNull();
    expect(screen.queryByText("对节点交差，还可再带一层子队。")).toBeNull();
    expect(screen.queryByText("对节点交差，不能再向下委派。")).toBeNull();
  });

  it("切换页签只换身份，不展览交付形态、不含按需目录", async () => {
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
      expect(screen.getByText("主 Agent 核。")).toBeTruthy();
    });
    expect(screen.queryByText("lead_subteam：子队拆法")).toBeNull();
    expect(screen.queryByText("队员交付合同")).toBeNull();
    expect(screen.queryByText("本节点交付形态")).toBeNull();
    expect(screen.queryByText(contract)).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "叶子队员" }));
    expect(screen.getByText("叶子身份。")).toBeTruthy();
    expect(screen.queryByText("主 Agent 核。")).toBeNull();
    expect(screen.queryByText("本节点交付形态")).toBeNull();
    expect(screen.queryByText(contract)).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "可再委派的队员" }));
    expect(screen.getByText("可再委派身份。")).toBeTruthy();
    expect(screen.queryByText("本节点交付形态")).toBeNull();
    expect(screen.queryByText(contract)).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "主 Agent" }));
    expect(screen.getByText("主 Agent 核。")).toBeTruthy();
    expect(screen.queryByText("本节点交付形态")).toBeNull();
    expect(screen.queryByText(contract)).toBeNull();
  });

  it("官方 HOW 只读，不能改这一条", async () => {
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
      expect(screen.getByText("合同审查")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("派单进阶"));
    await waitFor(() => {
      expect(screen.getByTestId("factory-skill-editor")).toBeTruthy();
    });
    expect(screen.queryByRole("button", { name: "换用" })).toBeNull();
    expect(screen.queryByRole("button", { name: "恢复出厂" })).toBeNull();
    expect(screen.queryByRole("button", { name: "编辑" })).toBeNull();
    expect(screen.queryByRole("button", { name: "保存" })).toBeNull();
  });

  it("没有范围选择器；目录不分来源区；常驻账号条目跟在身份后", async () => {
    vi.mocked(getCapabilities).mockResolvedValue(base);
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("主 Agent 身份正文")).toBeTruthy();
    });
    const nav = screen.getByRole("navigation", { name: "提示词目录" });
    expect(screen.queryByLabelText("技能目录范围")).toBeNull();
    expect(screen.queryByLabelText("提示词目录范围")).toBeNull();
    expect(screen.queryByText("常驻模板")).toBeNull();
    expect(screen.queryByText("按需注入")).toBeNull();
    expect(screen.queryByText("我的技能")).toBeNull();
    expect(within(nav).queryByText("自带")).toBeNull();
    expect(within(nav).queryByText("我的")).toBeNull();
    expect(within(nav).queryByText("其他")).toBeNull();
    expect(screen.queryByTestId("my-skills")).toBeNull();
    expect(screen.getByText("官方")).toBeTruthy();
    expect(within(nav).getByText("偏好")).toBeTruthy();
    expect(within(nav).getByText("画像")).toBeTruthy();
    expect(within(nav).getByText("派单进阶")).toBeTruthy();

    fireEvent.click(screen.getByText("偏好"));
    expect(await screen.findByTestId("account-entry-editor")).toBeTruthy();
    expect(screen.getByText("我的")).toBeTruthy();
    expect(screen.getByText("AI 可能改")).toBeTruthy();
    expect(
      within(screen.getByTestId("account-entry-editor")).queryByText("常驻"),
    ).toBeNull();
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
    fireEvent.click(await screen.findByRole("button", { name: "新建条目" }));
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
