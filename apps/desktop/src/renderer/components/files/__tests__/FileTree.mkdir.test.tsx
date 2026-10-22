// @vitest-environment jsdom
import { FileTree } from "@/components/files/FileTree";
import { TooltipProvider } from "@/components/ui/tooltip";
import { type FileNode, type FileSource, baseName } from "@/lib/fileSource";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/files/FileTreeRowMenu", () => ({
  FileTreeRowMenu: () => null,
}));
vi.mock("@/lib/toast", () => ({
  notifySuccess: vi.fn(),
  notifyError: vi.fn(),
  notifyActionError: vi.fn(),
  notifyWarning: vi.fn(),
  notifyInfo: vi.fn(),
}));
vi.mock("@/hooks/useFolders", () => ({ getFolders: vi.fn(() => []) }));

function makeSource(entries: FileNode[] = []): {
  source: FileSource;
  mkdir: ReturnType<typeof vi.fn>;
  del: ReturnType<typeof vi.fn>;
} {
  const mkdir = vi.fn(async (path: string) => {
    const name = baseName(path);
    entries.push({ path, name, isDir: true });
  });
  const del = vi.fn(async (path: string) => {
    const i = entries.findIndex((e) => e.path === path);
    if (i >= 0) entries.splice(i, 1);
  });
  return {
    mkdir,
    del,
    source: {
      id: "workspace:mkdir",
      label: "工作区",
      caps: { watch: false, transfer: false, edit: true, snapshots: false },
      listDir: async () => [...entries],
      read: async () => ({ kind: "text", text: "", truncated: false }),
      createFile: async () => {},
      mkdir,
      move: async () => {},
      delete: del,
    },
  };
}

beforeEach(() => {
  localStorage.clear();
});

function renderTree(source: FileSource) {
  return render(
    <TooltipProvider>
      <FileTree source={source} onOpenFile={vi.fn()} />
    </TooltipProvider>,
  );
}

describe("FileTree toolbar", () => {
  it("offers 新建文件 and 新建子文件夹, not Composer 新建文件夹", async () => {
    const { source } = makeSource();
    renderTree(source);
    await screen.findByText("暂无文件");
    expect(screen.getByRole("button", { name: "新建文件" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "新建子文件夹" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "新建文件夹" })).toBeNull();
  });

  it("新建子文件夹 lands 未命名文件夹 then rename; Esc deletes the empty dir", async () => {
    const { source, mkdir, del } = makeSource();
    renderTree(source);
    await screen.findByText("暂无文件");
    fireEvent.click(screen.getByRole("button", { name: "新建子文件夹" }));
    await waitFor(() => expect(mkdir).toHaveBeenCalledWith("未命名文件夹"));
    const input = await screen.findByDisplayValue("未命名文件夹");
    fireEvent.keyDown(input, { key: "Escape" });
    await waitFor(() => expect(del).toHaveBeenCalledWith("未命名文件夹"));
  });

  it("blur on the first rename keeps 未命名文件夹", async () => {
    const { source, mkdir, del } = makeSource();
    renderTree(source);
    await screen.findByText("暂无文件");
    fireEvent.click(screen.getByRole("button", { name: "新建子文件夹" }));
    await waitFor(() => expect(mkdir).toHaveBeenCalledWith("未命名文件夹"));
    const input = await screen.findByDisplayValue("未命名文件夹");
    fireEvent.blur(input);
    await waitFor(() => {
      expect(screen.queryByDisplayValue("未命名文件夹")).toBeNull();
    });
    expect(del).not.toHaveBeenCalled();
  });
});
