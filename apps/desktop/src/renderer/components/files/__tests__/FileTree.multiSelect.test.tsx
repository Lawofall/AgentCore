// @vitest-environment jsdom

import { FileTree } from "@/components/files/FileTree";
import {
  __resetFileClipboardForTests,
  getFileClipboard,
} from "@/components/files/fileClipboard";
import { DRAG_MIME } from "@/components/files/fileTreeDrag";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileNode, FileSource } from "@/lib/fileSource";
import { notifySuccess } from "@/lib/toast";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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

function file(name: string): FileNode {
  return { path: name, name, isDir: false };
}

/** 云端工作区那一档能力：可传输（下载）、可改（删除 / 移动）、有软删区。 */
function makeSource(
  fail: { on: string; reason: string } | null = null,
  /** `docs/` 里已有的文件名（用来造「目标位置已存在同名项」）。 */
  docsHas: string[] = [],
): FileSource & {
  deleted: string[];
  downloaded: string[];
  moved: [string, string][];
  listed: string[];
} {
  const deleted: string[] = [];
  const downloaded: string[] = [];
  const moved: [string, string][] = [];
  const listed: string[] = [];
  const reject = (path: string) => {
    if (fail && path === fail.on) throw new Error(fail.reason);
  };
  return {
    id: "workspace:multi",
    label: "工作区",
    caps: { watch: false, transfer: true, edit: true, snapshots: true },
    listDir: async (dir) => {
      listed.push(dir);
      if (dir === "")
        return [dirNode(), file("a.md"), file("b.md"), file("c.md")];
      if (dir === "docs") {
        return docsHas.map((name) => ({
          path: `docs/${name}`,
          name,
          isDir: false,
        }));
      }
      return [];
    },
    read: async () => ({ kind: "text", text: "", truncated: false }),
    createFile: async () => {},
    mkdir: async () => {},
    move: async (src, dst) => {
      reject(src);
      moved.push([src, dst]);
    },
    delete: async (path) => {
      reject(path);
      deleted.push(path);
    },
    download: async (path) => {
      reject(path);
      downloaded.push(path);
    },
    deleted,
    downloaded,
    moved,
    listed,
  };
}

/** 根被列过几次——用来等一次「什么也没发生」的粘贴真的跑完（它收尾会重拉根）。 */
function rootListings(source: { listed: string[] }): number {
  return source.listed.filter((dir) => dir === "").length;
}

function dirNode(): FileNode {
  return { path: "docs", name: "docs", isDir: true };
}

function renderTree(source: FileSource, onOpenFile = vi.fn()) {
  render(
    <TooltipProvider>
      <FileTree source={source} onOpenFile={onOpenFile} />
    </TooltipProvider>,
  );
  return { onOpenFile };
}

/** 选中若干行：首个普通点击（= 单选并打开），其余 Ctrl 加选。 */
async function select(...names: string[]) {
  const first = await screen.findByText(names[0]);
  fireEvent.click(first);
  for (const name of names.slice(1)) {
    fireEvent.click(screen.getByText(name), { ctrlKey: true });
  }
}

beforeEach(() => {
  vi.mocked(notifySuccess).mockClear();
  // 剪贴板是全局一份，别让上一条用例剪下的东西漏进下一条。
  __resetFileClipboardForTests();
  // 展开态按源 id 落盘，同样会漏（上一条展开了 docs/，下一条一挂载就是展开的）。
  localStorage.clear();
});

describe("文件树多选（对齐桌面文件管理器）", () => {
  it("Ctrl 加减选、Shift 连选、Esc 清空；带修饰键的点击不换预览", async () => {
    const { onOpenFile } = renderTree(makeSource());

    fireEvent.click(await screen.findByText("a.md"));
    expect(onOpenFile).toHaveBeenCalledWith("a.md", "a.md");
    // 单选不挂操作条：一项用不着批量。
    expect(screen.queryByText(/已选择/)).toBeNull();

    fireEvent.click(screen.getByText("c.md"), { ctrlKey: true });
    expect(screen.getByText("已选择 2 项")).toBeTruthy();
    // 加选没有把预览换成 c.md。
    expect(onOpenFile).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText("c.md"), { ctrlKey: true });
    expect(screen.queryByText(/已选择/)).toBeNull();

    // 锚点仍是 a.md：Shift 连到 c.md 即 a → b → c 三项。
    fireEvent.click(screen.getByText("a.md"));
    fireEvent.click(screen.getByText("c.md"), { shiftKey: true });
    expect(screen.getByText("已选择 3 项")).toBeTruthy();

    fireEvent.keyDown(screen.getByText("a.md"), { key: "Escape" });
    expect(screen.queryByText(/已选择/)).toBeNull();
  });

  it("批量删除逐项报账：失败项不中断整批，且列出是哪一项、为什么", async () => {
    const source = makeSource({ on: "b.md", reason: "目标被占用" });
    renderTree(source);
    await select("a.md", "b.md", "c.md");

    fireEvent.click(screen.getByRole("button", { name: /删除/ }));
    const confirm = await screen.findByRole("dialog");
    expect(within(confirm).getByText("删除选中的 3 项？")).toBeTruthy();
    // 软删承诺与单项删除同一句。
    expect(within(confirm).getByText(/可从软删区还原/)).toBeTruthy();
    expect(within(confirm).getByText("b.md")).toBeTruthy();
    fireEvent.click(within(confirm).getByRole("button", { name: "删除" }));

    const report = await screen.findByText("已删除 2 项，1 项失败");
    expect(report).toBeTruthy();
    expect(screen.getByText("目标被占用")).toBeTruthy();
    // 一项失败没有把后面的项吞掉。
    expect(source.deleted).toEqual(["a.md", "c.md"]);
    expect(notifySuccess).not.toHaveBeenCalled();
  });

  it("全部成功时只报一条成功，不弹清单", async () => {
    const source = makeSource();
    renderTree(source);
    await select("a.md", "b.md");

    fireEvent.click(screen.getByRole("button", { name: /删除/ }));
    const confirm = await screen.findByRole("dialog");
    fireEvent.click(within(confirm).getByRole("button", { name: "删除" }));

    await waitFor(() => expect(source.deleted).toEqual(["a.md", "b.md"]));
    expect(notifySuccess).toHaveBeenCalledWith("已删除 2 项");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("批量下载：文件夹整夹打成 zip，与文件一并成功", async () => {
    const source = makeSource();
    renderTree(source);
    fireEvent.click(await screen.findByText("docs"));
    await screen.findByText("空文件夹"); // 等展开这一层落定，免得懒加载在断言之后才回来
    fireEvent.click(screen.getByText("a.md"), { ctrlKey: true });

    fireEvent.click(screen.getByRole("button", { name: /下载/ }));

    await waitFor(() => expect(source.downloaded).toEqual(["docs", "a.md"]));
    expect(notifySuccess).toHaveBeenCalledWith("已下载 2 项");
    expect(screen.queryByText(/失败/)).toBeNull();
  });

  it("批量移动 = 剪切多项后粘贴到目标文件夹，部分失败照样逐项报", async () => {
    const source = makeSource({ on: "b.md", reason: "文件被占用" });
    renderTree(source);
    await select("a.md", "b.md");

    fireEvent.keyDown(screen.getByText("a.md"), { key: "x", ctrlKey: true });
    // 落点 = 当前锚点行（目录本身）。
    fireEvent.click(screen.getByText("docs"));
    fireEvent.keyDown(screen.getByText("docs"), { key: "v", ctrlKey: true });

    expect(await screen.findByText("已移动 1 项，1 项失败")).toBeTruthy();
    expect(screen.getByText("文件被占用")).toBeTruthy();
    expect(source.moved).toEqual([["a.md", "docs/a.md"]]);
  });

  it("整批都粘不动时保住剪贴板：换个地方还能粘，不静默把剪切吞掉", async () => {
    // docs/ 里已有同名项 → 两项都被挡下，一项也没搬走。
    const source = makeSource(null, ["a.md", "b.md"]);
    renderTree(source);
    await select("a.md", "b.md");

    fireEvent.keyDown(screen.getByText("a.md"), { key: "x", ctrlKey: true });
    fireEvent.click(screen.getByText("docs"));
    fireEvent.keyDown(screen.getByText("docs"), { key: "v", ctrlKey: true });

    expect(await screen.findByText("2 项移动失败")).toBeTruthy();
    expect(source.moved).toEqual([]);
    expect(getFileClipboard()).toEqual({
      op: "cut",
      sourceId: "workspace:multi",
      paths: ["a.md", "b.md"],
    });
  });

  it("整批原地粘贴什么也没发生：不报账，剪贴板照旧留着", async () => {
    const source = makeSource();
    renderTree(source);
    await select("a.md", "b.md");

    fireEvent.keyDown(screen.getByText("a.md"), { key: "x", ctrlKey: true });
    // 锚点是根下的文件 → 落点就是它们现在待的地方。
    fireEvent.keyDown(screen.getByText("a.md"), { key: "v", ctrlKey: true });
    // 等这一轮真的跑完：挂载列过一次根，粘贴自己再列一次（探同名）+ 收尾重拉一次。
    await waitFor(() => expect(rootListings(source)).toBeGreaterThanOrEqual(3));

    expect(source.moved).toEqual([]);
    expect(notifySuccess).not.toHaveBeenCalled();
    expect(getFileClipboard()).toEqual({
      op: "cut",
      sourceId: "workspace:multi",
      paths: ["a.md", "b.md"],
    });

    // 剪贴板没被吞掉，所以换到 docs/ 再粘就照样搬得动。
    fireEvent.click(screen.getByText("docs"));
    fireEvent.keyDown(screen.getByText("docs"), { key: "v", ctrlKey: true });

    await waitFor(() =>
      expect(source.moved).toEqual([
        ["a.md", "docs/a.md"],
        ["b.md", "docs/b.md"],
      ]),
    );
    // 原地那次没报账，只有真搬走的这次报了一条。
    expect(notifySuccess).toHaveBeenCalledTimes(1);
    expect(notifySuccess).toHaveBeenCalledWith("已移动 2 项");
    expect(getFileClipboard()).toBeNull();
  });
});

describe("拖拽多选（拖的是选区就搬整批）", () => {
  /** 捕获 dragstart 写进 dataTransfer 的那份载荷。 */
  function startDrag(name: string): { mime: string; raw: string } {
    const dataTransfer = { setData: vi.fn(), effectAllowed: "" };
    fireEvent.dragStart(screen.getByText(name), { dataTransfer });
    const [mime, raw] = dataTransfer.setData.mock.calls[0] as [string, string];
    return { mime, raw };
  }

  /** drop 时读的那份 dataTransfer（内部拖拽只认自定义 MIME）。 */
  function dropData(raw: string) {
    return {
      types: [DRAG_MIME],
      getData: (type: string) => (type === DRAG_MIME ? raw : ""),
      files: [],
      items: [],
    };
  }

  it("拖选区内的行 = 一次搬整批，落点逐项报账", async () => {
    const source = makeSource({ on: "b.md", reason: "文件被占用" });
    renderTree(source);
    await select("a.md", "b.md");

    const { mime, raw } = startDrag("a.md");
    expect(mime).toBe(DRAG_MIME);
    expect(JSON.parse(raw)).toEqual({
      sourceId: "workspace:multi",
      paths: ["a.md", "b.md"],
    });

    fireEvent.drop(screen.getByText("docs"), { dataTransfer: dropData(raw) });

    expect(await screen.findByText("已移动 1 项，1 项失败")).toBeTruthy();
    expect(screen.getByText("文件被占用")).toBeTruthy();
    expect(source.moved).toEqual([["a.md", "docs/a.md"]]);
    // 搬走的那项不再挂在选区上（留着会让下一次删除对着空路径开火）。
    await waitFor(() => expect(screen.queryByText(/已选择/)).toBeNull());
  });

  it("拖选区外的行只搬这一行，不牵连选区", async () => {
    const source = makeSource();
    renderTree(source);
    await select("a.md", "b.md");

    expect(JSON.parse(startDrag("c.md").raw)).toEqual({
      sourceId: "workspace:multi",
      paths: ["c.md"],
    });
  });

  it("选中父目录又选中它里面的文件时，只搬父目录（子项跟着走）", async () => {
    const source = makeSource(null, ["inner.md"]);
    renderTree(source);
    fireEvent.click(await screen.findByText("docs"));
    fireEvent.click(await screen.findByText("inner.md"), { ctrlKey: true });

    expect(JSON.parse(startDrag("docs").raw)).toEqual({
      sourceId: "workspace:multi",
      paths: ["docs"],
    });
  });
});
