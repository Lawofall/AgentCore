// @vitest-environment jsdom

import { FileTree } from "@/components/files/FileTree";
import { saveExpanded } from "@/components/files/fileTreeExpanded";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileNode, FileSource } from "@/lib/fileSource";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/files/FileTreeRowMenu", () => ({
  FileTreeRowMenu: () => null,
}));

function file(path: string): FileNode {
  return {
    path,
    name: path.slice(path.lastIndexOf("/") + 1),
    isDir: false,
  };
}

function dir(path: string): FileNode {
  return {
    path,
    name: path.slice(path.lastIndexOf("/") + 1),
    isDir: true,
  };
}

function stubSource(
  id: string,
  listing: Record<string, FileNode[]>,
  listed: string[],
): FileSource {
  return {
    id,
    label: id,
    caps: { watch: false, transfer: false, edit: false, snapshots: false },
    listDir: async (dir) => {
      listed.push(dir);
      return listing[dir] ?? [];
    },
    read: async () => ({ kind: "text", text: "", truncated: false }),
    createFile: async () => {},
    mkdir: async () => {},
    move: async () => {},
    delete: async () => {},
  };
}

function renderTree(source: FileSource) {
  return render(
    <TooltipProvider>
      <FileTree source={source} onOpenFile={() => {}} />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
});

describe("FileTree 持久化展开水合", () => {
  it("预置 saveExpanded 后挂载，对已展开路径发出 listDir 并露出子项", async () => {
    saveExpanded("local:hydrate", new Set(["docs", "docs/api"]));
    const listed: string[] = [];
    renderTree(
      stubSource(
        "local:hydrate",
        {
          "": [dir("docs"), file("readme.md")],
          docs: [dir("docs/api"), file("docs/guide.md")],
          "docs/api": [file("docs/api/spec.md")],
        },
        listed,
      ),
    );

    await waitFor(() => {
      expect(listed).toContain("");
      expect(listed).toContain("docs");
      expect(listed).toContain("docs/api");
    });
    expect(await screen.findByText("guide.md")).toBeTruthy();
    expect(await screen.findByText("spec.md")).toBeTruthy();
    expect(screen.queryByText("加载中…")).toBeNull();
  });

  it("换 source.id 不得拿旧源路径去新源拉", async () => {
    saveExpanded("local:src-a", new Set(["old-folder"]));
    saveExpanded("local:src-b", new Set(["new-folder"]));
    const listedA: string[] = [];
    const listedB: string[] = [];
    const srcA = stubSource(
      "local:src-a",
      {
        "": [dir("old-folder")],
        "old-folder": [file("old-folder/a.md")],
      },
      listedA,
    );
    const srcB = stubSource(
      "local:src-b",
      {
        "": [dir("new-folder")],
        "new-folder": [file("new-folder/b.md")],
      },
      listedB,
    );

    const { rerender } = renderTree(srcA);
    await waitFor(() => expect(listedA).toContain("old-folder"));
    expect(await screen.findByText("a.md")).toBeTruthy();

    rerender(
      <TooltipProvider>
        <FileTree source={srcB} onOpenFile={() => {}} />
      </TooltipProvider>,
    );

    await waitFor(() => expect(listedB).toContain("new-folder"));
    expect(listedB).not.toContain("old-folder");
    expect(await screen.findByText("b.md")).toBeTruthy();
    expect(screen.queryByText("a.md")).toBeNull();
  });
});
