import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock("@/services/refreshAccountRulesMemory", () => ({
  scheduleAccountRulesMemoryRefresh: vi.fn(),
}));

import { api } from "@/services/api";
import {
  createRuleDocument,
  deleteDocument,
  getAlwaysQuota,
  getDocument,
  listDocuments,
  listScopeEntries,
  listUserRules,
  renameDocument,
  updateDocumentApplyMode,
  writeDocument,
} from "@/services/documents";
import { scheduleAccountRulesMemoryRefresh } from "@/services/refreshAccountRulesMemory";

const node = (over: Record<string, unknown> = {}) => ({
  id: "n",
  parent_id: null,
  folder_id: null,
  kind: "document",
  role: "rule",
  ai_maintained: false,
  apply_mode: "always",
  description: "",
  name: "r.md",
  frontmatter_error: null,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("documents client", () => {
  it("listUserRules collects AgentCore/规则 leaves and leftover top-level rules", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === "/v1/documents") {
        return [
          node({ id: "legacy", folder_id: null, name: "旧顶层.md" }),
          node({
            id: "ac-g",
            kind: "folder",
            role: "general",
            name: "AgentCore",
            folder_id: null,
          }),
          node({
            id: "ac-p",
            kind: "folder",
            role: "general",
            name: "AgentCore",
            folder_id: "F1",
          }),
          node({ id: "m", ai_maintained: true, name: "画像.md" }),
          node({ id: "gen", role: "general", name: "note.md" }),
        ];
      }
      if (url === "/v1/documents?parent_id=ac-g") {
        return [
          node({
            id: "rules-g",
            kind: "folder",
            role: "general",
            name: "规则",
            parent_id: "ac-g",
          }),
        ];
      }
      if (url === "/v1/documents?parent_id=rules-g") {
        return [
          node({
            id: "g",
            folder_id: null,
            name: "全局.md",
            parent_id: "rules-g",
          }),
        ];
      }
      if (url === "/v1/documents?parent_id=ac-p") {
        return [
          node({
            id: "rules-p",
            kind: "folder",
            role: "general",
            name: "规则",
            parent_id: "ac-p",
            folder_id: "F1",
          }),
        ];
      }
      if (url === "/v1/documents?parent_id=rules-p") {
        return [
          node({
            id: "p",
            folder_id: "F1",
            name: "项目.md",
            parent_id: "rules-p",
          }),
        ];
      }
      return [];
    });

    const rules = await listUserRules();
    expect(rules.map((r) => r.id).sort()).toEqual(["g", "legacy", "p"]);
    expect(rules.find((r) => r.id === "g")).toMatchObject({
      folderId: null,
      name: "全局.md",
    });
    expect(rules.find((r) => r.id === "p")).toMatchObject({
      folderId: "F1",
      name: "项目.md",
    });
  });

  it("listScopeEntries flattens 规则 + 记忆 leaves without role grouping", async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === "/v1/documents") {
        return [
          node({
            id: "ac",
            kind: "folder",
            role: "general",
            name: "AgentCore",
          }),
        ];
      }
      if (url === "/v1/documents?parent_id=ac") {
        return [
          node({
            id: "rules",
            kind: "folder",
            role: "general",
            name: "规则",
            parent_id: "ac",
          }),
          node({
            id: "mem",
            kind: "folder",
            role: "general",
            name: "记忆",
            parent_id: "ac",
          }),
        ];
      }
      if (url === "/v1/documents?parent_id=rules") {
        return [
          node({
            id: "r1",
            name: "语气.md",
            description: "短硬",
            parent_id: "rules",
          }),
        ];
      }
      if (url === "/v1/documents?parent_id=mem") {
        return [
          node({
            id: "m1",
            name: "画像.md",
            ai_maintained: true,
            description: "画像摘要",
            parent_id: "mem",
          }),
        ];
      }
      return [];
    });

    const rows = await listScopeEntries(null);
    expect(rows.map((r) => r.id).sort()).toEqual(["m1", "r1"]);
    expect(rows.find((r) => r.id === "r1")).toMatchObject({
      description: "短硬",
      applyMode: "always",
    });
    expect(rows.find((r) => r.id === "m1")).toMatchObject({
      description: "画像摘要",
      aiMaintained: true,
    });
  });

  it("getAlwaysQuota maps percent + absolute chars + global/project split", async () => {
    vi.mocked(api.get).mockResolvedValue({
      used_chars: 12,
      max_chars: 100,
      percent: 12,
      global_chars: 7,
      project_chars: 5,
    });
    const q = await getAlwaysQuota("F1");
    expect(api.get).toHaveBeenCalledWith(
      "/v1/documents/always-quota?folder_id=F1",
    );
    expect(q).toEqual({
      usedChars: 12,
      maxChars: 100,
      percent: 12,
      globalChars: 7,
      projectChars: 5,
    });
  });

  it("listDocuments maps always_chars (null for on_demand)", async () => {
    vi.mocked(api.get).mockResolvedValue([
      node({ id: "a", apply_mode: "always", always_chars: 4200 }),
      node({ id: "b", apply_mode: "on_demand", always_chars: null }),
    ]);
    const rows = await listDocuments(null);
    expect(rows.find((r) => r.id === "a")?.alwaysChars).toBe(4200);
    expect(rows.find((r) => r.id === "b")?.alwaysChars).toBeNull();
  });

  it("getDocument maps description / frontmatter_error / quota_warning", async () => {
    vi.mocked(api.get).mockResolvedValue(
      node({
        id: "d1",
        content: "hello",
        version: "v9",
        description: "一行",
        frontmatter_error: "unclosed frontmatter",
        quota_warning: "over",
      }),
    );
    const doc = await getDocument("d1");
    expect(api.get).toHaveBeenCalledWith("/v1/documents/d1");
    expect(doc).toMatchObject({
      content: "hello",
      version: "v9",
      description: "一行",
      frontmatterError: "unclosed frontmatter",
      quotaWarning: "over",
    });
  });

  it("createRuleDocument posts a user rule pinned to a scope (folder_id)", async () => {
    vi.mocked(api.post).mockResolvedValue(node({ id: "new", content: "" }));
    await createRuleDocument("新规则.md", "F1");
    expect(api.post).toHaveBeenCalledWith("/v1/documents", {
      name: "新规则.md",
      kind: "document",
      role: "rule",
      content: "",
      parent_id: null,
      folder_id: "F1",
      apply_mode: "always",
    });
  });

  it("createRuleDocument defaults to the GLOBAL layer (folder_id null)", async () => {
    vi.mocked(api.post).mockResolvedValue(node({ id: "new" }));
    await createRuleDocument("g.md");
    expect(api.post).toHaveBeenCalledWith(
      "/v1/documents",
      expect.objectContaining({ folder_id: null }),
    );
  });

  it("writeDocument sends the content + CAS baseline and maps quota_warning", async () => {
    vi.mocked(api.put).mockResolvedValue({
      ok: true,
      version: "v2",
      conflict: false,
      quota_warning: "常驻超了",
    });
    const r = await writeDocument("d1", "body", "v1");
    expect(api.put).toHaveBeenCalledWith("/v1/documents/d1", {
      content: "body",
      baseline: "v1",
    });
    expect(r.quotaWarning).toBe("常驻超了");
    expect(scheduleAccountRulesMemoryRefresh).toHaveBeenCalledTimes(1);
  });

  it("writeDocument does not refresh the sidecar snapshot on a CAS conflict", async () => {
    vi.mocked(api.put).mockResolvedValue({
      ok: false,
      version: "v9",
      conflict: true,
    });
    await writeDocument("d1", "body", "v1");
    expect(scheduleAccountRulesMemoryRefresh).not.toHaveBeenCalled();
  });

  it("renameDocument patches the name", async () => {
    vi.mocked(api.patch).mockResolvedValue(node({ id: "d1", name: "改名.md" }));
    const r = await renameDocument("d1", "改名.md");
    expect(api.patch).toHaveBeenCalledWith("/v1/documents/d1", {
      name: "改名.md",
    });
    expect(r.name).toBe("改名.md");
  });

  it("updateDocumentApplyMode patches apply_mode (always|on_demand)", async () => {
    vi.mocked(api.patch).mockResolvedValue(
      node({ id: "d1", apply_mode: "on_demand" }),
    );
    const r = await updateDocumentApplyMode("d1", "on_demand");
    expect(api.patch).toHaveBeenCalledWith("/v1/documents/d1", {
      apply_mode: "on_demand",
    });
    expect(r.applyMode).toBe("on_demand");
  });

  it("maps wire non-always apply_mode onto on_demand for the UI", async () => {
    vi.mocked(api.get).mockResolvedValue(
      node({ id: "d1", apply_mode: "conditional", content: "", version: "v" }),
    );
    const doc = await getDocument("d1");
    expect(doc.applyMode).toBe("on_demand");
  });

  it("deleteDocument hits the delete endpoint", async () => {
    vi.mocked(api.delete).mockResolvedValue({
      ok: true,
      version: "",
      conflict: false,
    });
    await deleteDocument("d1");
    expect(api.delete).toHaveBeenCalledWith("/v1/documents/d1");
  });
});
