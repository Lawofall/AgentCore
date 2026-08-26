import { describe, expect, it } from "vitest";
import {
  findWorkspaceFilePaths,
  isWorkspaceFilePath,
  splitWorkspacePathText,
} from "../workspaceFilePath";

describe("isWorkspaceFilePath", () => {
  it("accepts convention-doc and src paths", () => {
    expect(isWorkspaceFilePath("AgentCore/文档/工作稿/白板PRD.md")).toBe(true);
    expect(isWorkspaceFilePath("src/auth/login.ts")).toBe(true);
    expect(isWorkspaceFilePath("site/index.html")).toBe(true);
  });

  it("rejects bare names, URLs, MIME, traversal", () => {
    expect(isWorkspaceFilePath("白板PRD.md")).toBe(false);
    expect(isWorkspaceFilePath("https://example.com/foo.md")).toBe(false);
    expect(isWorkspaceFilePath("text/plain")).toBe(false);
    expect(isWorkspaceFilePath("image/png")).toBe(false);
    expect(isWorkspaceFilePath("foo/bar")).toBe(false);
    expect(isWorkspaceFilePath("../secret.md")).toBe(false);
    expect(isWorkspaceFilePath("HTTP/2")).toBe(false);
  });
});

describe("findWorkspaceFilePaths", () => {
  it("finds a path in a Chinese sentence", () => {
    const hits = findWorkspaceFilePaths(
      "已写入 AgentCore/文档/工作稿/白板PRD.md。",
    );
    expect(hits.map((h) => h.path)).toEqual([
      "AgentCore/文档/工作稿/白板PRD.md",
    ]);
  });

  it("does not take a slice of an https URL", () => {
    expect(
      findWorkspaceFilePaths("见 https://cdn.example.com/foo/bar.md"),
    ).toEqual([]);
  });

  it("splits surrounding punctuation", () => {
    const parts = splitWorkspacePathText("（src/a.ts）");
    expect(parts).toEqual([
      { type: "text", value: "（" },
      { type: "path", value: "src/a.ts" },
      { type: "text", value: "）" },
    ]);
  });
});
