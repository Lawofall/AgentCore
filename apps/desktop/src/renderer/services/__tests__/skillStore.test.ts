import { api } from "@/services/api";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  installSkill,
  listSkillStore,
  publishSkill,
  reportSkill,
  skillStoreListQuery,
} from "../skillStore";

vi.mock("@/services/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/api")>();
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      delete: vi.fn(),
    },
  };
});

vi.mock("@/services/refreshAccountRulesMemory", () => ({
  scheduleAccountRulesMemoryRefresh: vi.fn(),
}));

const apiGet = vi.mocked(api.get);
const apiPost = vi.mocked(api.post);

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
});

describe("skillStore", () => {
  it("列表带 q 与分页，不含正文", async () => {
    expect(skillStoreListQuery({ q: "合同", page: 2 })).toBe(
      "?q=%E5%90%88%E5%90%8C&page=2&page_size=24",
    );
    apiGet.mockResolvedValue({
      data: [
        {
          id: "l1",
          name: "合同审查",
          description: "审合同时用",
          author: "ssauthor",
          version_n: 1,
          installed: true,
          has_update: false,
          source_document_id: "d1",
        },
      ],
      page: 2,
      page_size: 24,
      total: 1,
    });
    const page = await listSkillStore({ q: "合同", page: 2 });
    expect(apiGet).toHaveBeenCalledWith(
      "/v1/skill-store?q=%E5%90%88%E5%90%8C&page=2&page_size=24",
    );
    expect(page.items[0]?.installed).toBe(true);
    expect(page.items[0]?.version).toBe("1");
    expect(page.items[0]?.documentId).toBe("d1");
    expect(page.items[0]).not.toHaveProperty("content");
  });

  it("安装走 POST /install，并刷新账号目录缓存", async () => {
    const { scheduleAccountRulesMemoryRefresh } = await import(
      "@/services/refreshAccountRulesMemory"
    );
    apiPost.mockResolvedValue({
      id: "l1",
      name: "合同审查",
      description: "审合同时用",
      author: "ssauthor",
      version_n: 1,
      installed: true,
      has_update: false,
      source_document_id: "d1",
      document_id: "copy-1",
    });
    await installSkill("l1");
    expect(apiPost).toHaveBeenCalledWith("/v1/skill-store/l1/install");
    expect(scheduleAccountRulesMemoryRefresh).toHaveBeenCalled();
  });

  it("上架与举报不走换槽路径", async () => {
    apiPost.mockResolvedValue({
      id: "l1",
      name: "合同审查",
      description: "审合同时用",
      author: "ssauthor",
      version_n: 1,
      installed: false,
      has_update: false,
      source_document_id: "d1",
    });
    await publishSkill("d1");
    expect(apiPost).toHaveBeenCalledWith("/v1/skill-store", {
      document_id: "d1",
    });
    apiPost.mockResolvedValue(undefined);
    await reportSkill("l1", "垃圾");
    expect(apiPost).toHaveBeenCalledWith("/v1/skill-store/l1/reports", {
      reason: "垃圾",
    });
    expect(
      apiPost.mock.calls.every(
        (call) => !String(call[0]).includes("replacements"),
      ),
    ).toBe(true);
  });
});
