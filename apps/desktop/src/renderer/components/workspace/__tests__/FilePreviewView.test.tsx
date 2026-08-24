// @vitest-environment jsdom

import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileSource } from "@/lib/fileSource";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// md 预览复用聊天 Markdown 渲染器；桩成可断言的叶子，避免在 jsdom 里拉整条 remark 管线。
vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({
    content,
    fileSource,
  }: {
    content: string;
    fileSource?: FileSource;
  }) => (
    <div data-testid="md-render" data-source-id={fileSource?.id ?? ""}>
      {content}
    </div>
  ),
}));

import { FilePreviewView } from "@/components/workspace/FilePreviewView";

const HTML_TEXT = "<html><body><script>x</script>hi</body></html>";

function makeSource(overrides: Partial<FileSource> = {}): FileSource {
  return {
    id: "workspace:c1",
    label: "工作区",
    caps: { watch: false, transfer: true, edit: true, snapshots: true },
    listDir: async () => [],
    read: async () => ({ kind: "text", text: HTML_TEXT, truncated: false }),
    createFile: async () => {},
    mkdir: async () => {},
    move: async () => {},
    delete: async () => {},
    // 可编辑的源都成对提供 CAS 编辑契约（云端 / 本地 / 记忆 / 条目源皆然）。
    readForEdit: async () => ({
      text: HTML_TEXT,
      version: { mtimeMs: 100 },
      encoding: "utf-8" as const,
      eol: "lf" as const,
    }),
    writeText: async () => ({ ok: true as const, version: { mtimeMs: 200 } }),
    writeBytes: async () => {},
    download: async () => {},
    ...overrides,
  } as FileSource;
}

function renderView(source: FileSource, name = "index.html") {
  return render(
    <TooltipProvider>
      <FilePreviewView
        source={source}
        path={name}
        name={name}
        onClose={vi.fn()}
      />
    </TooltipProvider>,
  );
}

describe("FilePreviewView — HTML 源码视图（静态快照已取消）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("HTML 与普通文本一致显示源码，不再渲染快照 iframe，也无「预览/源码」切换", async () => {
    const { container } = renderView(makeSource());
    expect(await screen.findByText(HTML_TEXT)).toBeTruthy();
    expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector("pre")?.textContent).toBe(HTML_TEXT);
    expect(screen.queryByRole("button", { name: "查看源码" })).toBeNull();
    expect(screen.queryByRole("button", { name: "预览效果" })).toBeNull();
    expect(screen.queryByText(/这是网页文件的源码/)).toBeNull();
  });

  it("编辑回归：HTML 可编辑（铅笔入口），不再渲染写入归因", async () => {
    renderView(makeSource());
    expect(await screen.findByRole("button", { name: "编辑" })).toBeTruthy();
    expect(screen.queryByText(/写入归因/)).toBeNull();
  });

  it("标题栏最高档：有 openInAppPreview →「完整预览」，点击路由到位", async () => {
    const openInAppPreview = vi.fn().mockResolvedValue(undefined);
    const openInBrowser = vi.fn().mockResolvedValue(undefined);
    renderView(makeSource({ openInAppPreview, openInBrowser }));
    await screen.findByText(HTML_TEXT);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "完整预览" }));
    });
    expect(openInAppPreview).toHaveBeenCalledWith("index.html");
    expect(openInBrowser).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "在浏览器打开" })).toBeTruthy();
  });

  it("标题栏次档：无 openInAppPreview 有 openInBrowser →「在浏览器打开」", async () => {
    const openInBrowser = vi.fn().mockResolvedValue(undefined);
    renderView(makeSource({ openInBrowser }));
    await screen.findByText(HTML_TEXT);

    expect(screen.queryByRole("button", { name: "完整预览" })).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "在浏览器打开" }));
    });
    expect(openInBrowser).toHaveBeenCalledWith("index.html");
  });

  it("web 兜底：两出口都无 → 标题栏只剩下载，无完整预览/浏览器入口", async () => {
    const download = vi.fn().mockResolvedValue(undefined);
    renderView(makeSource({ download }));
    await screen.findByText(HTML_TEXT);

    expect(screen.queryByRole("button", { name: "完整预览" })).toBeNull();
    expect(screen.queryByRole("button", { name: "在浏览器打开" })).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "下载文件" }));
    });
    expect(download).toHaveBeenCalledWith("index.html", "index.html");
  });

  it("非 HTML 文本不挂 HTML 专用入口", async () => {
    renderView(makeSource(), "notes.txt");
    expect(await screen.findByText(HTML_TEXT)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "完整预览" })).toBeNull();
    expect(screen.queryByRole("button", { name: "在浏览器打开" })).toBeNull();
  });
});

describe("FilePreviewView — 无法预览的文件给可点出口", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const readBinary = async () =>
    ({ kind: "binary", mime: "application/zip", size: 2048 }) as const;

  it("谓词放行：兜底面主按钮直接开外部程序（头部图标同套门控）", async () => {
    const openWithOsDefaultApp = vi.fn().mockResolvedValue(undefined);
    renderView(
      makeSource({
        read: readBinary,
        openWithOsDefaultApp,
        canOpenWithOsDefaultApp: () => true,
      }),
      "bundle.zip",
    );
    await screen.findByText("无法预览此文件");

    const entries = screen.getAllByRole("button", { name: "用默认程序打开" });
    expect(entries).toHaveLength(2); // 头部图标 + 兜底主按钮
    await act(async () => {
      fireEvent.click(entries[1]);
    });
    expect(openWithOsDefaultApp).toHaveBeenCalledWith("bundle.zip");
  });

  it("谓词拒绝（白名单外）：头部与兜底面都不给该入口，只剩下载", async () => {
    const download = vi.fn().mockResolvedValue(undefined);
    renderView(
      makeSource({
        read: readBinary,
        openWithOsDefaultApp: vi.fn(),
        canOpenWithOsDefaultApp: () => false,
        download,
      }),
      "tool.exe",
    );
    await screen.findByText("无法预览此文件");

    expect(screen.queryByRole("button", { name: "用默认程序打开" })).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "下载" }));
    });
    expect(download).toHaveBeenCalledWith("tool.exe", "tool.exe");
  });
});

/**
 * 非 md 文本的面板内编辑必须与 md 编辑器同一套 CAS 语汇：读全文带版本基线、保存前比对、
 * 冲突给「重新加载 / 仍然覆盖」——回归的是「同回合 Agent 在写同一个文件时静默覆盖」。
 */
describe("FilePreviewView — 非 md 文本编辑走 CAS（不静默覆盖）", () => {
  const TEXT = "line one\nline two\n";

  beforeEach(() => {
    vi.clearAllMocks();
  });

  function textSource(overrides: Partial<FileSource> = {}): FileSource {
    return makeSource({
      read: async () => ({ kind: "text", text: TEXT, truncated: false }),
      readForEdit: async () => ({
        text: TEXT,
        version: { mtimeMs: 100 },
        encoding: "utf-8" as const,
        eol: "crlf" as const,
      }),
      ...overrides,
    });
  }

  /** 渲染 → 点铅笔 → 等 readForEdit 落地，返回编辑区。 */
  async function enterEdit(source: FileSource): Promise<HTMLTextAreaElement> {
    renderView(source, "notes.txt");
    await screen.findByRole("button", { name: "编辑" });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    });
    return screen.getByRole("textbox") as HTMLTextAreaElement;
  }

  it("正文与基线取自 readForEdit，保存经 writeText 带基线（不再裸 writeBytes）", async () => {
    const writeText = vi.fn(async () => ({
      ok: true as const,
      version: { mtimeMs: 200 },
    }));
    const writeBytes = vi.fn(async () => {});
    const source = textSource({ writeText, writeBytes });

    const textarea = await enterEdit(source);
    expect(textarea.value).toBe(TEXT);

    fireEvent.change(textarea, { target: { value: "edited" } });
    await act(async () => {
      fireEvent.click(screen.getByText("保存"));
    });

    expect(writeText).toHaveBeenCalledWith("notes.txt", {
      content: "edited",
      encoding: "utf-8",
      eol: "crlf", // 原文换行随基线回写，不被编辑框归一
      baseline: { mtimeMs: 100 },
    });
    expect(writeBytes).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull(); // 存完回预览
  });

  it("磁盘已改动 → 冲突横幅 + 保留草稿；「仍然覆盖」以磁盘版本为基线重写", async () => {
    const writeText = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        reason: "conflict",
        version: { mtimeMs: 999 },
      })
      .mockResolvedValueOnce({ ok: true, version: { mtimeMs: 1000 } });
    const source = textSource({ writeText });

    const textarea = await enterEdit(source);
    fireEvent.change(textarea, { target: { value: "我的改动" } });
    await act(async () => {
      fireEvent.click(screen.getByText("保存"));
    });

    expect(screen.getByText(/保存会覆盖磁盘版本/)).toBeTruthy();
    const conflictBar = screen.getByText(/保存会覆盖磁盘版本/).closest("div");
    expect(conflictBar?.className).toContain("primary");
    expect(conflictBar?.className).not.toContain("destructive");
    expect(screen.getByText("重新加载").className).not.toContain("destructive");
    expect(screen.getByText("仍然覆盖").className).toContain("destructive");
    // 冲突不是终点：仍在编辑态、草稿还在，用户才有得选。
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
      "我的改动",
    );

    await act(async () => {
      fireEvent.click(screen.getByText("仍然覆盖"));
    });

    expect(writeText).toHaveBeenNthCalledWith(
      2,
      "notes.txt",
      expect.objectContaining({
        content: "我的改动",
        baseline: { mtimeMs: 999 },
      }),
    );
    expect(screen.queryByText(/保存会覆盖磁盘版本/)).toBeNull();
  });

  it("冲突后「重新加载」确认后丢弃草稿，取磁盘上的最新正文继续编辑", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    try {
      const readForEdit = vi
        .fn()
        .mockResolvedValueOnce({
          text: TEXT,
          version: { mtimeMs: 100 },
          encoding: "utf-8",
          eol: "lf",
        })
        .mockResolvedValueOnce({
          text: "Agent 刚写进去的内容",
          version: { mtimeMs: 999 },
          encoding: "utf-8",
          eol: "lf",
        });
      const source = textSource({
        readForEdit,
        writeText: vi.fn(async () => ({
          ok: false as const,
          reason: "conflict" as const,
          version: { mtimeMs: 999 },
        })),
      });

      const textarea = await enterEdit(source);
      fireEvent.change(textarea, { target: { value: "我的改动" } });
      await act(async () => {
        fireEvent.click(screen.getByText("保存"));
      });
      await act(async () => {
        fireEvent.click(screen.getByText("重新加载"));
      });

      expect(confirmSpy).toHaveBeenCalled(); // 草稿不会无声蒸发
      expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
        "Agent 刚写进去的内容",
      );
      expect(screen.queryByText(/保存会覆盖磁盘版本/)).toBeNull();
    } finally {
      confirmSpy.mockRestore();
    }
  });

  it("写入被拒 → 内联说明并留在编辑态，不冒充保存成功", async () => {
    const source = textSource({
      writeText: vi.fn(async () => ({
        ok: false as const,
        reason: "denied" as const,
      })),
    });

    const textarea = await enterEdit(source);
    fireEvent.change(textarea, { target: { value: "改了" } });
    await act(async () => {
      fireEvent.click(screen.getByText("保存"));
    });

    const saveBar = screen.getByText("没有写入权限，无法保存").closest("div");
    expect(saveBar?.className).toContain("bg-muted/40");
    expect(saveBar?.className).not.toContain("destructive");
    expect(screen.getByRole("textbox")).toBeTruthy();
  });

  it("源没有 CAS 编辑对（只有 writeBytes）→ 连编辑入口都不给", async () => {
    renderView(
      textSource({ readForEdit: undefined, writeText: undefined }),
      "notes.txt",
    );
    await screen.findByText(/line two/);
    expect(screen.queryByRole("button", { name: "编辑" })).toBeNull();
  });

  it("截断预览仍不可编辑（保存会丢尾巴）", async () => {
    renderView(
      textSource({
        read: async () => ({ kind: "text", text: TEXT, truncated: true }),
      }),
      "big.txt",
    );
    await screen.findByText(/内容较大/);
    expect(screen.queryByRole("button", { name: "编辑" })).toBeNull();
  });
});

describe("FilePreviewView — Markdown 默认渲染预览（阅读优先）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("md 点开默认渲染预览，而非源码 pre", async () => {
    const md = "# 标题\n\n正文段落";
    const { container } = renderView(
      makeSource({
        id: "workspace:preview-md",
        read: async () => ({ kind: "text", text: md, truncated: false }),
      }),
      "notes.md",
    );
    const rendered = await screen.findByTestId("md-render");
    expect(rendered.textContent).toBe(md);
    expect(rendered.getAttribute("data-source-id")).toBe(
      "workspace:preview-md",
    );
    expect(container.querySelector("pre")).toBeNull(); // 不落源码视图
  });

  it("截断的 md 回落源码 + 截断提示（避免半截 markdown 渲染错乱）", async () => {
    const { container } = renderView(
      makeSource({
        read: async () => ({
          kind: "text",
          text: "# 很长的文档",
          truncated: true,
        }),
      }),
      "big.md",
    );
    await screen.findByText(/内容较大/);
    expect(container.querySelector("pre")).toBeTruthy();
    expect(screen.queryByTestId("md-render")).toBeNull();
  });
});
