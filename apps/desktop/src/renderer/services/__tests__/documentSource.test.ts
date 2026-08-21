import { beforeEach, describe, expect, it, vi } from "vitest";

// The document FileSource is a thin path-aware dispatcher over the REST client: the tab path
// IS the document id, so readForEdit/writeText must call the matching service with that id and
// forward the CAS baseline. Mock the service so the test pins the dispatch (the only logic).
vi.mock("@/services/documents", () => ({
  getDocument: vi.fn(() =>
    Promise.resolve({
      id: "d1",
      parentId: null,
      folderId: null,
      kind: "document",
      role: "rule",
      aiMaintained: false,
      applyMode: "always",
      description: "",
      name: "用户规则.md",
      frontmatterError: null,
      content: "body",
      version: "v1",
      quotaWarning: null,
    }),
  ),
  writeDocument: vi.fn(() =>
    Promise.resolve({
      ok: true,
      version: "v2",
      conflict: false,
      frontmatterError: null,
      quotaWarning: null,
    }),
  ),
}));

vi.mock("@/lib/toast", () => ({
  notifyWarning: vi.fn(),
}));

import { getDocument, writeDocument } from "@/services/documents";
import { createDocumentSource } from "@/services/sources/documentSource";

const src = createDocumentSource();

beforeEach(() => {
  vi.clearAllMocks();
});

describe("documentSource", () => {
  it("advertises an editable, non-transfer source", () => {
    expect(src.caps.edit).toBe(true);
    expect(src.caps.transfer).toBe(false);
    expect(src.readForEdit).toBeDefined();
    expect(src.writeText).toBeDefined();
  });

  it("readForEdit loads the doc by its id (= the tab path) and carries the CAS etag", async () => {
    const doc = await src.readForEdit?.("d1");
    expect(getDocument).toHaveBeenCalledWith("d1");
    expect(doc).toEqual({
      text: "body",
      version: { etag: "v1" },
      encoding: "utf-8",
      eol: "lf",
    });
  });

  it("writeText forwards the id + content + baseline etag", async () => {
    const r = await src.writeText?.("d1", {
      content: "next",
      encoding: "utf-8",
      eol: "lf",
      baseline: { etag: "v1" },
    });
    expect(writeDocument).toHaveBeenCalledWith("d1", "next", "v1");
    expect(r).toEqual({ ok: true, version: { etag: "v2" } });
  });

  it("writeText treats a missing baseline as an unconditional write (null)", async () => {
    await src.writeText?.("d1", {
      content: "x",
      encoding: "utf-8",
      eol: "lf",
      baseline: null,
    });
    expect(writeDocument).toHaveBeenCalledWith("d1", "x", null);
  });

  it("maps a write conflict into the source-agnostic conflict result", async () => {
    vi.mocked(writeDocument).mockResolvedValueOnce({
      ok: false,
      version: "live",
      conflict: true,
      frontmatterError: null,
      quotaWarning: null,
    });
    const r = await src.writeText?.("d1", {
      content: "y",
      encoding: "utf-8",
      eol: "lf",
      baseline: { etag: "stale" },
    });
    expect(r).toEqual({
      ok: false,
      reason: "conflict",
      version: { etag: "live" },
    });
  });

  it("toasts quota_warning on a successful over-cap edit", async () => {
    const { notifyWarning } = await import("@/lib/toast");
    vi.mocked(writeDocument).mockResolvedValueOnce({
      ok: true,
      version: "v3",
      conflict: false,
      frontmatterError: null,
      quotaWarning: "常驻条目已超配额",
    });
    await src.writeText?.("d1", {
      content: "big",
      encoding: "utf-8",
      eol: "lf",
      baseline: { etag: "v1" },
    });
    expect(notifyWarning).toHaveBeenCalledWith(
      "AI 暂时记不下新东西",
      expect.objectContaining({
        description: "常驻条目已超配额",
        action: expect.objectContaining({ label: "去整理" }),
      }),
    );
  });

  it("toasts frontmatter_error on a successful save that demoted the entry", async () => {
    const { notifyWarning } = await import("@/lib/toast");
    vi.mocked(writeDocument).mockResolvedValueOnce({
      ok: true,
      version: "v4",
      conflict: false,
      frontmatterError: "unclosed frontmatter",
      quotaWarning: null,
    });
    await src.writeText?.("d1", {
      content: "---\nbad",
      encoding: "utf-8",
      eol: "lf",
      baseline: { etag: "v1" },
    });
    expect(notifyWarning).toHaveBeenCalledWith(
      "规则格式有问题，这条暂时不按「每次生效」注入",
      expect.objectContaining({
        description: "unclosed frontmatter",
      }),
    );
  });

  it("rejects tree / CRUD ops the editor never uses (listed directly by the rail instead)", async () => {
    await expect(src.createFile("x")).rejects.toThrow();
    await expect(src.mkdir("x")).rejects.toThrow();
    await expect(src.move("a", "b")).rejects.toThrow();
    await expect(src.delete("x")).rejects.toThrow();
    await expect(src.listDir("")).resolves.toEqual([]);
  });
});
