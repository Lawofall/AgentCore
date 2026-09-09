import { api } from "@/services/api";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  installWorkflow,
  listInstalledWorkflows,
  listWorkflowStore,
  publishWorkflow,
  reportWorkflow,
  workflowStoreListQuery,
} from "../workflowStore";

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

const apiGet = vi.mocked(api.get);
const apiPost = vi.mocked(api.post);

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
});

describe("workflowStore", () => {
  it("列表带 q 与分页，不含 definition", async () => {
    expect(workflowStoreListQuery({ q: "周报", page: 2 })).toBe(
      "?q=%E5%91%A8%E6%8A%A5&page=2&page_size=24",
    );
    apiGet.mockResolvedValue({
      data: [
        {
          id: "l1",
          name: "周报流水线",
          description: "每周写周报",
          author: "wfauthor",
          version_n: 1,
          installed: true,
          has_update: false,
          source_workflow_id: "w1",
        },
      ],
      page: 2,
      page_size: 24,
      total: 1,
    });
    const page = await listWorkflowStore({ q: "周报", page: 2 });
    expect(apiGet).toHaveBeenCalledWith(
      "/v1/workflow-store?q=%E5%91%A8%E6%8A%A5&page=2&page_size=24",
    );
    expect(page.items[0]?.installed).toBe(true);
    expect(page.items[0]?.version).toBe("1");
    expect(page.items[0]?.workflowId).toBe("w1");
    expect(page.items[0]?.installWorkflowId).toBeNull();
    expect(page.items[0]).not.toHaveProperty("definition");
  });

  it("安装走 POST /install，不刷新提示词目录", async () => {
    apiPost.mockResolvedValue({
      id: "l1",
      name: "周报流水线",
      description: "每周写周报",
      author: "wfauthor",
      version_n: 1,
      installed: true,
      has_update: false,
      source_workflow_id: "w1",
      workflow_id: "copy-1",
    });
    await installWorkflow("l1");
    expect(apiPost).toHaveBeenCalledWith("/v1/workflow-store/l1/install");
  });

  it("已安装列表用 workflow_id 当本机副本", async () => {
    apiGet.mockResolvedValue({
      data: [
        {
          id: "l1",
          name: "周报流水线",
          description: "每周写周报",
          author: "wfauthor",
          version_n: 1,
          installed: true,
          has_update: false,
          source_workflow_id: "src",
          workflow_id: "copy-1",
        },
      ],
    });
    const rows = await listInstalledWorkflows();
    expect(apiGet).toHaveBeenCalledWith("/v1/workflow-store/installed");
    expect(rows[0]?.workflowId).toBe("src");
    expect(rows[0]?.installWorkflowId).toBe("copy-1");
  });

  it("上架与举报走 workflow-store，不上 skill-store", async () => {
    apiPost.mockResolvedValue({
      id: "l1",
      name: "周报流水线",
      description: "每周写周报",
      author: "wfauthor",
      version_n: 1,
      installed: false,
      has_update: false,
      source_workflow_id: "w1",
    });
    await publishWorkflow("w1");
    expect(apiPost).toHaveBeenCalledWith("/v1/workflow-store", {
      workflow_id: "w1",
    });
    apiPost.mockResolvedValue(undefined);
    await reportWorkflow("l1", "垃圾");
    expect(apiPost).toHaveBeenCalledWith("/v1/workflow-store/l1/reports", {
      reason: "垃圾",
    });
    expect(
      apiPost.mock.calls.every(
        (call) => !String(call[0]).includes("skill-store"),
      ),
    ).toBe(true);
  });
});
