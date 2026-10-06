/**
 * 本机工作区根读盘：越界 403、缺失 404、未绑定不挡云端、状态按 URL 分键。
 */
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  bindLocalWorkspaceRoot,
  consumeWorkspaceDocumentStatus,
  noteWorkspaceDocumentStatus,
  readBoundWorkspaceFile,
  resetLocalWorkspaceFilesForTests,
  resolveFileUnderRoot,
} from "../browser/workspace-local-files";

describe("resolveFileUnderRoot", () => {
  it("rejects traversal and keeps a nested file", () => {
    const root = mkdtempSync(join(tmpdir(), "ac-ws-"));
    expect(resolveFileUnderRoot(root, "../etc/passwd")).toBeNull();
    expect(resolveFileUnderRoot(root, "nested/ok.html")).toBe(
      join(root, "nested", "ok.html"),
    );
  });
});

describe("readBoundWorkspaceFile", () => {
  afterEach(() => {
    resetLocalWorkspaceFilesForTests();
  });

  it("serves a file under the bound root", async () => {
    const root = mkdtempSync(join(tmpdir(), "ac-ws-"));
    mkdirSync(join(root, "nested"));
    writeFileSync(join(root, "nested", "ok.html"), "<p>ok</p>", "utf8");
    expect(bindLocalWorkspaceRoot("c1", root)).toBe(true);

    const hit = await readBoundWorkspaceFile("c1", "nested/ok.html");
    expect(hit.kind).toBe("file");
    if (hit.kind !== "file") return;
    expect(hit.status).toBe(200);
    expect(new TextDecoder().decode(hit.body)).toBe("<p>ok</p>");
    expect(hit.mime).toContain("html");
  });

  it("404 when the file is missing and 403 on traversal", async () => {
    const root = mkdtempSync(join(tmpdir(), "ac-ws-"));
    expect(bindLocalWorkspaceRoot("c1", root)).toBe(true);
    expect(await readBoundWorkspaceFile("c1", "missing.html")).toEqual({
      kind: "error",
      status: 404,
    });
    expect(await readBoundWorkspaceFile("c1", "../secret.txt")).toEqual({
      kind: "error",
      status: 403,
    });
  });

  it("stays unbound when the root is relative or absent", async () => {
    expect(bindLocalWorkspaceRoot("c1", "relative/dir")).toBe(false);
    expect(await readBoundWorkspaceFile("c1", "index.html")).toEqual({
      kind: "unbound",
    });
  });
});

describe("workspace document status", () => {
  afterEach(() => {
    resetLocalWorkspaceFilesForTests();
  });

  it("does not let a subresource status replace the document", () => {
    noteWorkspaceDocumentStatus("c1", "workspace://conv.c1/index.html", 200);
    noteWorkspaceDocumentStatus("c1", "workspace://conv.c1/a.css", 404);
    expect(
      consumeWorkspaceDocumentStatus("c1", "workspace://conv.c1/index.html"),
    ).toBe(200);
    expect(
      consumeWorkspaceDocumentStatus("c1", "workspace://conv.c1/a.css"),
    ).toBe(404);
  });
});
