import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("@/api/client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));

import {
  DocumentsApiError,
  MEMORY_DIR_NAME,
  createRuleDocument,
  deleteDocument,
  getAlwaysQuota,
  getDocument,
  isDocumentsUnavailable,
  listDocuments,
  listScopeEntries,
  listUserRules,
  renameDocument,
  toApplyMode,
  updateDocumentApplyMode,
  writeDocument,
} from "../documents";

function okJson(body: unknown, status = 200) {
  return {
    ok: true,
    status,
    json: async () => body,
  };
}

function fail(status: number) {
  return { ok: false, status, json: async () => ({}) };
}

const node = (over: Record<string, unknown> = {}) => ({
  id: "r1",
  parent_id: null,
  folder_id: null,
  kind: "document",
  role: "rule",
  ai_maintained: false,
  apply_mode: "always",
  name: "语气规则.md",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  ...over,
});

const mapped = (over: Record<string, unknown> = {}) => ({
  id: "r1",
  parentId: null,
  folderId: null,
  kind: "document",
  role: "rule",
  aiMaintained: false,
  applyMode: "always",
  description: "",
  name: "语气规则.md",
  frontmatterError: null,
  alwaysChars: null,
  disputedAt: null,
  ...over,
});

function mockDocumentsByUrl(table: Record<string, unknown>) {
  apiFetch.mockImplementation(async (path: string) => {
    if (path in table) return okJson(table[path]);
    return okJson([]);
  });
}

beforeEach(() => {
  apiFetch.mockReset();
});

describe("toApplyMode", () => {
  it("maps always; everything else (incl. empty, conditional) → on_demand", () => {
    expect(toApplyMode("always")).toBe("always");
    expect(toApplyMode("on_demand")).toBe("on_demand");
    expect(toApplyMode("conditional")).toBe("on_demand");
    expect(toApplyMode("")).toBe("on_demand");
  });
});

describe("listDocuments / listUserRules", () => {
  it("GET /v1/documents → camelCase nodes (incl. new entry fields)", async () => {
    apiFetch.mockResolvedValue(
      okJson([
        node({
          apply_mode: "on_demand",
          description: "  短硬  ",
          frontmatter_error: " unclosed ",
          always_chars: 42,
          disputed_at: " 2026-02-01T00:00:00Z ",
        }),
      ]),
    );
    await expect(listDocuments(null)).resolves.toEqual([
      mapped({
        applyMode: "on_demand",
        description: "短硬",
        frontmatterError: "unclosed",
        alwaysChars: 42,
        disputedAt: "2026-02-01T00:00:00Z",
      }),
    ]);
    expect(apiFetch).toHaveBeenCalledWith("/v1/documents");
  });

  it("listDocuments with parent_id query", async () => {
    apiFetch.mockResolvedValue(okJson([]));
    await listDocuments("p1");
    expect(apiFetch).toHaveBeenCalledWith("/v1/documents?parent_id=p1");
  });

  it("listUserRules walks AgentCore/规则 and keeps GLOBAL only", async () => {
    apiFetch
      .mockResolvedValueOnce(
        okJson([
          node({ id: "top-global", name: "遗留.md" }),
          node({
            id: "top-project",
            name: "项目遗留.md",
            folder_id: "F1",
          }),
          {
            id: "ac",
            parent_id: null,
            folder_id: null,
            kind: "folder",
            role: "general",
            ai_maintained: false,
            apply_mode: "always",
            name: "AgentCore",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
        ]),
      )
      .mockResolvedValueOnce(
        okJson([
          {
            id: "rd",
            parent_id: "ac",
            folder_id: null,
            kind: "folder",
            role: "general",
            ai_maintained: false,
            apply_mode: "always",
            name: "规则",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
          },
        ]),
      )
      .mockResolvedValueOnce(
        okJson([
          node({ id: "g1", name: "语气规则.md", apply_mode: "always" }),
          node({
            id: "p1",
            name: "项目规则.md",
            folder_id: "F1",
            apply_mode: "on_demand",
          }),
        ]),
      );

    const rows = await listUserRules();
    expect(rows.map((r) => r.id).sort()).toEqual(["g1", "top-global"]);
    expect(rows.find((r) => r.id === "p1")).toBeUndefined();
    expect(rows.find((r) => r.id === "top-project")).toBeUndefined();
    expect(rows.find((r) => r.id === "g1")).toMatchObject({
      applyMode: "always",
      description: "",
      frontmatterError: null,
      alwaysChars: null,
      disputedAt: null,
    });
  });

  it("HTTP 非 2xx → DocumentsApiError", async () => {
    apiFetch.mockResolvedValue(fail(500));
    await expect(listDocuments()).rejects.toBeInstanceOf(DocumentsApiError);
  });
});

describe("listScopeEntries", () => {
  it("flattens 规则 + 记忆 (incl. one nested level) + leftover tops; excludes other scopes", async () => {
    expect(MEMORY_DIR_NAME).toBe("记忆");
    mockDocumentsByUrl({
      "/v1/documents": [
        node({ id: "top-global", name: "遗留.md", role: "general" }),
        node({
          id: "top-project",
          name: "项目遗留.md",
          folder_id: "F1",
        }),
        node({
          id: "ac",
          kind: "folder",
          role: "general",
          name: "AgentCore",
        }),
        node({
          id: "ac-p",
          kind: "folder",
          role: "general",
          name: "AgentCore",
          folder_id: "F1",
        }),
      ],
      "/v1/documents?parent_id=ac": [
        node({
          id: "rd",
          parent_id: "ac",
          kind: "folder",
          role: "general",
          name: "规则",
        }),
        node({
          id: "md",
          parent_id: "ac",
          kind: "folder",
          role: "general",
          name: MEMORY_DIR_NAME,
        }),
        node({
          id: "other-dir",
          parent_id: "ac",
          kind: "folder",
          role: "general",
          name: "其它",
        }),
        node({
          id: "ac-loose",
          parent_id: "ac",
          name: "根下散落.md",
          apply_mode: "conditional",
        }),
      ],
      "/v1/documents?parent_id=rd": [
        node({
          id: "g1",
          parent_id: "rd",
          name: "语气规则.md",
          description: "短硬",
        }),
        node({
          id: "p1",
          parent_id: "rd",
          name: "项目规则.md",
          folder_id: "F1",
        }),
        node({
          id: "nested-dir",
          parent_id: "rd",
          kind: "folder",
          role: "general",
          name: "场景",
        }),
      ],
      "/v1/documents?parent_id=nested-dir": [
        node({
          id: "nested",
          parent_id: "nested-dir",
          name: "嵌套.md",
          description: "一层",
        }),
      ],
      "/v1/documents?parent_id=md": [
        node({
          id: "mem1",
          parent_id: "md",
          name: "画像.md",
          role: "general",
          ai_maintained: true,
          description: "画像摘要",
        }),
      ],
      "/v1/documents?parent_id=other-dir": [
        node({
          id: "hidden",
          parent_id: "other-dir",
          name: "不该收入.md",
        }),
      ],
      "/v1/documents?parent_id=ac-p": [
        node({
          id: "rd-p",
          parent_id: "ac-p",
          folder_id: "F1",
          kind: "folder",
          role: "general",
          name: "规则",
        }),
      ],
      "/v1/documents?parent_id=rd-p": [
        node({
          id: "proj-rule",
          parent_id: "rd-p",
          folder_id: "F1",
          name: "项目层.md",
        }),
      ],
    });

    const rows = await listScopeEntries(null);
    expect(rows.map((r) => r.id).sort()).toEqual([
      "ac-loose",
      "g1",
      "mem1",
      "nested",
      "top-global",
    ]);
    expect(rows.find((r) => r.id === "top-project")).toBeUndefined();
    expect(rows.find((r) => r.id === "p1")).toBeUndefined();
    expect(rows.find((r) => r.id === "hidden")).toBeUndefined();
    expect(rows.find((r) => r.id === "proj-rule")).toBeUndefined();
    expect(rows.find((r) => r.id === "g1")).toMatchObject({
      description: "短硬",
      applyMode: "always",
    });
    expect(rows.find((r) => r.id === "mem1")).toMatchObject({
      description: "画像摘要",
      aiMaintained: true,
      role: "general",
    });
    expect(rows.find((r) => r.id === "ac-loose")).toMatchObject({
      applyMode: "on_demand",
    });
  });
});

describe("getAlwaysQuota", () => {
  it("GET /v1/documents/always-quota maps percent + chars + global/project split", async () => {
    apiFetch.mockResolvedValue(
      okJson({
        used_chars: 12,
        max_chars: 100,
        percent: 12,
        global_chars: 7,
        project_chars: 5,
      }),
    );
    const q = await getAlwaysQuota("F1");
    expect(apiFetch).toHaveBeenCalledWith(
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

  it("falls back when split fields are missing (global vs project)", async () => {
    apiFetch.mockResolvedValueOnce(
      okJson({ used_chars: 20, max_chars: 100, percent: 20 }),
    );
    await expect(getAlwaysQuota(null)).resolves.toEqual({
      usedChars: 20,
      maxChars: 100,
      percent: 20,
      globalChars: 20,
      projectChars: 0,
    });
    expect(apiFetch).toHaveBeenCalledWith("/v1/documents/always-quota");

    apiFetch.mockResolvedValueOnce(
      okJson({
        used_chars: 20,
        max_chars: 100,
        percent: 20,
        global_chars: 8,
      }),
    );
    await expect(getAlwaysQuota("F1")).resolves.toEqual({
      usedChars: 20,
      maxChars: 100,
      percent: 20,
      globalChars: 8,
      projectChars: 12,
    });
  });
});

describe("create / apply_mode / write / rename / delete", () => {
  it("POST createRuleDocument defaults apply_mode=always", async () => {
    apiFetch.mockResolvedValue(
      okJson({
        ...node({ id: "new", name: "新规则.md" }),
        content: "",
        version: "v1",
      }),
    );
    const doc = await createRuleDocument("新规则.md");
    expect(doc.applyMode).toBe("always");
    expect(doc).toMatchObject({
      description: "",
      frontmatterError: null,
      alwaysChars: null,
      disputedAt: null,
    });
    expect(apiFetch).toHaveBeenCalledWith("/v1/documents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "新规则.md",
        kind: "document",
        role: "rule",
        content: "",
        parent_id: null,
        folder_id: null,
        apply_mode: "always",
      }),
    });
  });

  it("PATCH updateDocumentApplyMode", async () => {
    apiFetch.mockResolvedValue(
      okJson({
        ...node({ apply_mode: "on_demand" }),
        content: "x",
        version: "v2",
      }),
    );
    await expect(
      updateDocumentApplyMode("r1", "on_demand"),
    ).resolves.toMatchObject({
      id: "r1",
      applyMode: "on_demand",
    });
    expect(apiFetch).toHaveBeenCalledWith("/v1/documents/r1", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ apply_mode: "on_demand", reparent: false }),
    });
  });

  it("GET getDocument + PUT writeDocument", async () => {
    apiFetch
      .mockResolvedValueOnce(
        okJson({ ...node(), content: "hello", version: "v1" }),
      )
      .mockResolvedValueOnce(
        okJson({ ok: true, conflict: false, version: "v2" }),
      );
    await expect(getDocument("r1")).resolves.toMatchObject({
      content: "hello",
      version: "v1",
      description: "",
      frontmatterError: null,
    });
    await expect(writeDocument("r1", "hi", "v1")).resolves.toEqual({
      ok: true,
      conflict: false,
      version: "v2",
      frontmatterError: null,
      quotaWarning: null,
    });
    expect(apiFetch).toHaveBeenLastCalledWith("/v1/documents/r1", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: "hi", baseline: "v1" }),
    });
  });

  it("writeDocument maps quota_warning / frontmatter_error", async () => {
    apiFetch.mockResolvedValueOnce(
      okJson({
        ok: true,
        conflict: false,
        version: "v3",
        quota_warning: "常驻超了",
        frontmatter_error: "unclosed",
      }),
    );
    await expect(writeDocument("r1", "hi", "v1")).resolves.toEqual({
      ok: true,
      conflict: false,
      version: "v3",
      quotaWarning: "常驻超了",
      frontmatterError: "unclosed",
    });
  });

  it("renameDocument + deleteDocument", async () => {
    apiFetch
      .mockResolvedValueOnce(
        okJson({
          ...node({ name: "新名.md" }),
          content: "",
          version: "v1",
        }),
      )
      .mockResolvedValueOnce(
        okJson({ ok: true, conflict: false, version: "v1" }),
      );
    await expect(renameDocument("r1", "新名.md")).resolves.toMatchObject({
      name: "新名.md",
    });
    await expect(deleteDocument("r1")).resolves.toMatchObject({
      ok: true,
      frontmatterError: null,
      quotaWarning: null,
    });
    expect(apiFetch).toHaveBeenLastCalledWith("/v1/documents/r1", {
      method: "DELETE",
      headers: undefined,
      body: undefined,
    });
  });
});

describe("isDocumentsUnavailable", () => {
  it("404/501 → true; others false", () => {
    expect(isDocumentsUnavailable(new DocumentsApiError(404, "x"))).toBe(true);
    expect(isDocumentsUnavailable(new DocumentsApiError(501, "x"))).toBe(true);
    expect(isDocumentsUnavailable(new DocumentsApiError(500, "x"))).toBe(false);
    expect(isDocumentsUnavailable(new Error("x"))).toBe(false);
  });
});
