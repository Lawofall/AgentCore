import { __resetCapabilitiesCacheForTests } from "@/components/tools/useCapabilities";
import type { Capabilities } from "@/services/capabilities";
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
import { GuidelinesPage } from "../GuidelinesPage";

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

vi.mock("@/services/skillCatalog", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/services/skillCatalog")>();
  return {
    ...actual,
    getSkillCatalog: vi.fn(),
    replaceSkillSlot: vi.fn(),
    restoreSkillSlot: vi.fn(),
    muteSkillSlot: vi.fn(),
    unmuteSkillSlot: vi.fn(),
  };
});

vi.mock("@/services/skillStore", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/skillStore")>();
  return {
    ...actual,
    listMySkillListings: vi.fn(async () => []),
    publishSkill: vi.fn(),
    publishSkillVersion: vi.fn(),
    unpublishSkill: vi.fn(),
  };
});

const { getCapabilities } = await import("@/services/capabilities");
const { getSkillCatalog, replaceSkillSlot, muteSkillSlot } = await import(
  "@/services/skillCatalog"
);
const {
  listMySkillListings,
  publishSkill,
  publishSkillVersion,
  unpublishSkill,
} = await import("@/services/skillStore");

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
  packs: [],
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
      occupies: [],
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
  vi.mocked(replaceSkillSlot).mockReset();
  vi.mocked(muteSkillSlot).mockReset();
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
    status: "published",
  });
  vi.mocked(publishSkillVersion).mockReset();
  vi.mocked(unpublishSkill).mockReset();
  vi.mocked(unpublishSkill).mockResolvedValue(undefined);
});

afterEach(cleanup);

function renderPage() {
  return render(
    <MemoryRouter>
      <GuidelinesPage />
    </MemoryRouter>,
  );
}

describe("我的技能上架入口", () => {
  it("可写 mine 行有上架，不走换槽 API", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("合同审查"));
    fireEvent.click(await screen.findByRole("button", { name: "上架" }));
    await waitFor(() => {
      expect(publishSkill).toHaveBeenCalledWith("d1");
    });
    expect(replaceSkillSlot).not.toHaveBeenCalled();
    expect(muteSkillSlot).not.toHaveBeenCalled();
  });

  it("已上架的可写下架，仍不换槽", async () => {
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
        status: "published",
      },
    ]);
    renderPage();
    fireEvent.click(await screen.findByText("合同审查"));
    expect(await screen.findByText("已上架")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "下架" }));
    await waitFor(() => {
      expect(unpublishSkill).toHaveBeenCalledWith("listing-1");
    });
    expect(replaceSkillSlot).not.toHaveBeenCalled();
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
        status: "unpublished",
      },
    ]);
    renderPage();
    fireEvent.click(await screen.findByText("合同审查"));
    expect(screen.queryByText("已上架")).toBeNull();
    expect(screen.queryByRole("button", { name: "下架" })).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "上架" }));
    await waitFor(() => {
      expect(publishSkill).toHaveBeenCalledWith("d1");
    });
    expect(publishSkillVersion).not.toHaveBeenCalled();
  });
});
