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
  useFolders: () => [],
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
const {
  listMySkillListings,
  publishSkill,
  publishSkillVersion,
  unpublishSkill,
} = await import("@/services/skillStore");
const { listScopeEntries } = await import("@/services/documents");

const base: Capabilities = {
  guidelines: {
    shared_base: "共享准则正文",
    worker_leaf: "叶子身份正文",
    worker_captain: "可再委派队员身份正文",
    ceo_addon: "主 Agent 身份正文",
    ceo: "完整 CEO 提示词",
  },
  skills: [],
  tools: [],
};

const mineCatalog = {
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
};

beforeEach(() => {
  __resetCapabilitiesCacheForTests();
  vi.mocked(getCapabilities).mockReset();
  vi.mocked(getCapabilities).mockResolvedValue(base);
  vi.mocked(getSkillCatalog).mockReset();
  vi.mocked(getSkillCatalog).mockResolvedValue(mineCatalog);
  vi.mocked(listMySkillListings).mockReset();
  vi.mocked(listMySkillListings).mockResolvedValue([]);
  vi.mocked(publishSkill).mockReset();
  vi.mocked(publishSkill).mockResolvedValue({
    id: "listing-1",
    name: "合同审查",
    description: "审合同时用",
    author: "me",
    version: "1",
    installed: false,
    hasUpdate: false,
    documentId: "d1",
    installDocumentId: null,
    status: "published",
    group: "writing",
  });
  vi.mocked(publishSkillVersion).mockReset();
  vi.mocked(unpublishSkill).mockReset();
  vi.mocked(unpublishSkill).mockResolvedValue(undefined);
  vi.mocked(listScopeEntries).mockReset();
  vi.mocked(listScopeEntries).mockResolvedValue([]);
});

afterEach(cleanup);

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <GuidelinesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("我的提示词上架入口", () => {
  async function openMineItem() {
    fireEvent.click(await screen.findByText("合同审查", { exact: false }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByText("合同审查"));
    return dialog;
  }

  it("可写 mine 行有上架", async () => {
    renderPage();
    await openMineItem();
    fireEvent.click(await screen.findByRole("button", { name: "上架" }));
    const dialog = await screen.findByRole("dialog", { name: "上架到市场" });
    fireEvent.click(within(dialog).getByRole("button", { name: "写作成稿" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "上架" }));
    await waitFor(() => {
      expect(publishSkill).toHaveBeenCalledWith("d1", "writing");
    });
  });

  it("已上架的可写下架", async () => {
    vi.mocked(listMySkillListings).mockResolvedValue([
      {
        id: "listing-1",
        name: "合同审查",
        description: "审合同时用",
        author: "me",
        version: "1",
        installed: false,
        hasUpdate: false,
        documentId: "d1",
        installDocumentId: null,
        status: "published",
        group: "writing",
      },
    ]);
    renderPage();
    await openMineItem();
    expect(await screen.findByText("已上架")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "下架" }));
    await waitFor(() => {
      expect(unpublishSkill).toHaveBeenCalledWith("listing-1");
    });
  });

  it("作者下架后再上架走 POST /skill-store，不走 /versions", async () => {
    vi.mocked(listMySkillListings).mockResolvedValue([
      {
        id: "listing-1",
        name: "合同审查",
        description: "审合同时用",
        author: "me",
        version: "1",
        installed: false,
        hasUpdate: false,
        documentId: "d1",
        installDocumentId: null,
        status: "unpublished",
        group: "writing",
      },
    ]);
    renderPage();
    await openMineItem();
    expect(screen.queryByText("已上架")).toBeNull();
    expect(screen.queryByRole("button", { name: "下架" })).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "上架" }));
    const dialog = await screen.findByRole("dialog", { name: "上架到市场" });
    fireEvent.click(within(dialog).getByRole("button", { name: "上架" }));
    await waitFor(() => {
      expect(publishSkill).toHaveBeenCalledWith("d1", "writing");
    });
    expect(publishSkillVersion).not.toHaveBeenCalled();
  });
});
