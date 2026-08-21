// @vitest-environment jsdom
/**
 * Host state-machine tests for MarkdownFileEditor.
 *
 * The CodeMirror inner editor is replaced with a controllable stub (it needs a real
 * EditorView / DOM layout we don't exercise here), so these tests drive the host's
 * orchestration directly: load via readForEdit, GBK read-only, debounced autosave +
 * coalescing, CAS conflict + "仍然覆盖", and the AI-rewrite flow (capture selection →
 * call backend → enter merge review, with selection-drift rejection). FileSource and
 * the rewrite service are faked so nothing touches IPC / the network.
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import type { FileSource, FileSourceCaps } from "@/lib/fileSource";
import { rewriteSelection } from "@/services/rewrite";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MarkdownFileEditor } from "../MarkdownFileEditor";

// --- controllable inner-editor stub (module-level so the mock factory + tests share it) ---

let lastEditorProps: {
  onChange?: (v: string) => void;
  onSave?: () => void;
  initialDoc?: string;
};
let editorValue: string;
let selectionCtx: {
  from: number;
  to: number;
  selection: string;
  contextBefore: string;
  contextAfter: string;
} | null;
const editorHandle: Record<string, unknown> = {};

vi.mock("@/components/markdown/MarkdownSourceEditor", async () => {
  const React = await import("react");
  return {
    MarkdownSourceEditor: React.forwardRef(function Stub(
      props: {
        onChange?: (v: string) => void;
        onSave?: () => void;
        initialDoc?: string;
      },
      ref: React.Ref<unknown>,
    ) {
      lastEditorProps = props;
      React.useImperativeHandle(ref, () => editorHandle);
      return React.createElement("div", { "data-testid": "cm-stub" });
    }),
  };
});

// Keep heavy children out of jsdom: the preview renderer + the toolbar.
vi.mock("@/components/chat/Markdown", async () => {
  const React = await import("react");
  return {
    Markdown: ({ content }: { content: string }) =>
      React.createElement("div", { "data-testid": "md-preview" }, content),
  };
});
vi.mock("@/components/markdown/sourceToolbar", () => ({
  SourceToolbar: () => null,
}));
vi.mock("@/services/rewrite", () => ({ rewriteSelection: vi.fn() }));

const CAPS: FileSourceCaps = {
  watch: false,
  transfer: false,
  edit: true,
  snapshots: false,
};

function makeSource(over: Partial<FileSource> = {}): FileSource {
  return {
    id: "local:test",
    label: "Test",
    caps: CAPS,
    listDir: vi.fn(),
    read: vi.fn(),
    createFile: vi.fn(),
    mkdir: vi.fn(),
    move: vi.fn(),
    delete: vi.fn(),
    readForEdit: vi.fn(async () => ({
      text: "initial content",
      version: { mtimeMs: 100 },
      encoding: "utf-8" as const,
      eol: "lf" as const,
    })),
    writeText: vi.fn(async () => ({
      ok: true as const,
      version: { mtimeMs: 200 },
    })),
    ...over,
  } as FileSource;
}

function renderEditor(source: FileSource) {
  return render(
    <TooltipProvider>
      <MarkdownFileEditor
        source={source}
        path="a.md"
        name="a.md"
        onClose={() => {}}
      />
    </TooltipProvider>,
  );
}

/** Render + flush the async readForEdit so the host has loaded. Default view is now
 * 预览 (阅读优先), so the source editor is NOT mounted yet. */
async function renderLoaded(source: FileSource) {
  renderEditor(source);
  await act(async () => {});
}

/** renderLoaded + switch to 编辑 mode — where autosave / 保存 / AI 改写 live. */
async function renderEditing(source: FileSource) {
  await renderLoaded(source);
  await act(async () => {
    fireEvent.click(screen.getByText("编辑"));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  lastEditorProps = {};
  editorValue = "initial content";
  selectionCtx = {
    from: 0,
    to: 3,
    selection: "abc",
    contextBefore: "",
    contextAfter: "",
  };
  editorHandle.getValue = () => editorValue;
  editorHandle.getView = () => null;
  editorHandle.getSelectionContext = () => selectionCtx;
  editorHandle.startRewriteReview = vi.fn(() => true);
  editorHandle.endRewriteReview = vi.fn();
});

afterEach(() => {
  cleanup();
});

describe("MarkdownFileEditor host", () => {
  it("loads via readForEdit; defaults to 预览, and entering 编辑 mounts the editor", async () => {
    const source = makeSource();
    await renderLoaded(source);

    expect(source.readForEdit).toHaveBeenCalledWith("a.md");
    // 阅读优先：点开默认预览态，源码编辑器尚未挂载。
    expect(screen.queryByTestId("cm-stub")).toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByText("编辑"));
    });
    expect(screen.getByTestId("cm-stub")).toBeTruthy();
  });

  it("opens a GBK file read-only and never writes back", async () => {
    const source = makeSource({
      readForEdit: vi.fn(async () => ({
        text: "中文",
        version: { mtimeMs: 1 },
        encoding: "gbk" as const,
        eol: "lf" as const,
      })),
    });
    await renderEditing(source);

    expect(screen.getByText(/GBK 编码/)).toBeTruthy();
    expect(screen.queryByText("保存")).toBeNull(); // no save affordance when read-only

    // An edit must not schedule/perform a write (read-only gate + GBK guard in doSave).
    editorValue = "改了";
    act(() => lastEditorProps.onChange?.("改了"));
    expect(source.writeText).not.toHaveBeenCalled();
  });

  it("manual Save writes the current text with the load baseline", async () => {
    const source = makeSource();
    await renderEditing(source);

    editorValue = "edited";
    act(() => lastEditorProps.onChange?.("edited"));

    await act(async () => {
      fireEvent.click(screen.getByText("保存"));
    });

    expect(source.writeText).toHaveBeenCalledTimes(1);
    expect(source.writeText).toHaveBeenCalledWith(
      "a.md",
      expect.objectContaining({
        content: "edited",
        encoding: "utf-8",
        eol: "lf",
        baseline: { mtimeMs: 100 },
      }),
    );
  });

  it("debounced autosave fires once after the idle window and coalesces rapid edits", async () => {
    vi.useFakeTimers();
    try {
      const source = makeSource();
      renderEditor(source);
      await act(async () => {}); // flush load
      await act(async () => {
        fireEvent.click(screen.getByText("编辑"));
      });

      editorValue = "v1";
      act(() => lastEditorProps.onChange?.("v1"));
      editorValue = "v2";
      act(() => lastEditorProps.onChange?.("v2")); // resets the debounce window

      expect(source.writeText).not.toHaveBeenCalled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500);
      });

      expect(source.writeText).toHaveBeenCalledTimes(1);
      expect(source.writeText).toHaveBeenCalledWith(
        "a.md",
        expect.objectContaining({ content: "v2" }),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("surfaces a CAS conflict and 仍然覆盖 rewrites with the disk version as the baseline", async () => {
    const writeText = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        reason: "conflict",
        version: { mtimeMs: 999 },
      })
      .mockResolvedValueOnce({ ok: true, version: { mtimeMs: 1000 } });
    const source = makeSource({ writeText });
    await renderEditing(source);

    editorValue = "edited";
    act(() => lastEditorProps.onChange?.("edited"));
    await act(async () => {
      fireEvent.click(screen.getByText("保存"));
    });

    expect(screen.getByText(/保存会覆盖磁盘版本/)).toBeTruthy();
    const conflictBar = screen.getByText(/保存会覆盖磁盘版本/).closest("div");
    expect(conflictBar?.className).toContain("primary");
    expect(conflictBar?.className).not.toContain("destructive");
    expect(screen.getByText("重新加载").className).not.toContain("destructive");
    expect(screen.getByText("仍然覆盖").className).toContain("destructive");

    await act(async () => {
      fireEvent.click(screen.getByText("仍然覆盖"));
    });

    expect(writeText).toHaveBeenNthCalledWith(
      2,
      "a.md",
      expect.objectContaining({ baseline: { mtimeMs: 999 } }),
    );
  });

  it("save denial uses noticeChipNeutral, not destructive", async () => {
    const source = makeSource({
      writeText: vi.fn(async () => ({
        ok: false as const,
        reason: "denied" as const,
      })),
    });
    await renderEditing(source);
    editorValue = "edited";
    act(() => lastEditorProps.onChange?.("edited"));
    await act(async () => {
      fireEvent.click(screen.getByText("保存"));
    });
    const saveBar = screen.getByText("没有写入权限，无法保存").closest("div");
    expect(saveBar?.className).toContain("bg-muted/40");
    expect(saveBar?.className).not.toContain("destructive");
  });

  it("load failure 无法打开 is muted, not destructive", async () => {
    const source = makeSource({
      readForEdit: vi.fn(async () => {
        throw new Error("disk missing");
      }),
    });
    await renderLoaded(source);
    const title = screen.getByText("无法打开");
    expect(title.className).toContain("text-muted-foreground");
    expect(title.className).not.toContain("destructive");
    expect(screen.getByText("disk missing")).toBeTruthy();
  });

  it("AI 改写 without a selection shows a hint instead of opening the bar", async () => {
    selectionCtx = null;
    const source = makeSource();
    await renderEditing(source);

    await act(async () => {
      fireEvent.click(screen.getByText("AI 改写"));
    });

    expect(screen.getByText("请先选中要改写的文本")).toBeTruthy();
  });

  it("submits a rewrite with the captured selection context and enters merge review", async () => {
    selectionCtx = {
      from: 0,
      to: 3,
      selection: "abc",
      contextBefore: "BEFORE",
      contextAfter: "AFTER",
    };
    vi.mocked(rewriteSelection).mockResolvedValue("ABC");
    const source = makeSource();
    await renderEditing(source);

    await act(async () => {
      fireEvent.click(screen.getByText("AI 改写"));
    });
    fireEvent.change(screen.getByPlaceholderText(/想怎么改这段/), {
      target: { value: "更正式" },
    });
    await act(async () => {
      fireEvent.click(screen.getByText("改写"));
    });

    expect(rewriteSelection).toHaveBeenCalledWith({
      selection: "abc",
      instruction: "更正式",
      contextBefore: "BEFORE",
      contextAfter: "AFTER",
    });
    expect(editorHandle.startRewriteReview).toHaveBeenCalledWith(
      { from: 0, to: 3, selection: "abc" },
      "ABC",
    );
    expect(screen.getByText("完成")).toBeTruthy(); // review bar is up
  });

  it("rejects landing the rewrite when the selection drifted (startRewriteReview=false)", async () => {
    editorHandle.startRewriteReview = vi.fn(() => false);
    vi.mocked(rewriteSelection).mockResolvedValue("ABC");
    const source = makeSource();
    await renderEditing(source);

    await act(async () => {
      fireEvent.click(screen.getByText("AI 改写"));
    });
    fireEvent.change(screen.getByPlaceholderText(/想怎么改这段/), {
      target: { value: "更正式" },
    });
    await act(async () => {
      fireEvent.click(screen.getByText("改写"));
    });

    expect(screen.getByText("选区已改变，请重新选择后再试")).toBeTruthy();
    expect(screen.queryByText("完成")).toBeNull(); // never entered review
  });
});

describe("MarkdownFileEditor 「用默认程序打开」入口门控", () => {
  const label = "用默认程序打开";

  it("源不带谓词（本地源）→ 照旧出现，行为不变", async () => {
    await renderLoaded(makeSource({ openWithOsDefaultApp: vi.fn() }));
    expect(screen.getByLabelText(label)).toBeTruthy();
  });

  it("谓词放行 → 出现并调用源方法；谓词拒绝（白名单外）→ 入口不渲染", async () => {
    const openWithOsDefaultApp = vi.fn(async () => {});
    await renderLoaded(
      makeSource({
        openWithOsDefaultApp,
        canOpenWithOsDefaultApp: () => true,
      }),
    );
    await act(async () => {
      fireEvent.click(screen.getByLabelText(label));
    });
    expect(openWithOsDefaultApp).toHaveBeenCalledWith("a.md");

    cleanup();
    await renderLoaded(
      makeSource({
        openWithOsDefaultApp: vi.fn(),
        canOpenWithOsDefaultApp: () => false,
      }),
    );
    expect(screen.queryByLabelText(label)).toBeNull();
  });

  it("源没有 openWithOsDefaultApp（web 云端源）→ 入口不渲染", async () => {
    await renderLoaded(makeSource({ canOpenWithOsDefaultApp: () => true }));
    expect(screen.queryByLabelText(label)).toBeNull();
  });
});

const MEMORY_EMPTY_HINT =
  "AI 会把记得的内容写在这里，你也可以直接改或删除。";

const RETIRED_CHROME = `# 用户记忆
> 本文件由 AI 自动维护，你可随时编辑或删除任何条目。
`;

function makeMemorySource(text: string): FileSource {
  return makeSource({
    id: "memory",
    readForEdit: vi.fn(async () => ({
      text,
      version: { etag: "v1" },
      encoding: "utf-8" as const,
      eol: "lf" as const,
    })),
  });
}

describe("MarkdownFileEditor memory 预览空状态", () => {
  it("空正文预览出空状态，且不写盘", async () => {
    const source = makeMemorySource("");
    await renderLoaded(source);

    expect(screen.getByText(MEMORY_EMPTY_HINT)).toBeTruthy();
    expect(screen.queryByTestId("md-preview")).toBeNull();
    expect(source.writeText).not.toHaveBeenCalled();
  });

  it("chrome-only 预览出空状态，且不写盘", async () => {
    const source = makeMemorySource(RETIRED_CHROME);
    await renderLoaded(source);

    expect(screen.getByText(MEMORY_EMPTY_HINT)).toBeTruthy();
    expect(screen.queryByTestId("md-preview")).toBeNull();
    expect(source.writeText).not.toHaveBeenCalled();
  });

  it("有 ## / bullet 时剥壳后照常渲染，不出空状态", async () => {
    const source = makeMemorySource(`${RETIRED_CHROME}
## 沟通偏好
- 用中文
`);
    await renderLoaded(source);

    expect(screen.queryByText(MEMORY_EMPTY_HINT)).toBeNull();
    expect(screen.getByTestId("md-preview").textContent).toBe(
      "## 沟通偏好\n- 用中文",
    );
    expect(source.writeText).not.toHaveBeenCalled();
  });

  it("导航一句话定位不剥，也不出空状态", async () => {
    const nav = "# 导航\n一句话：示例仓\n";
    const source = makeMemorySource(nav);
    await renderLoaded(source);

    expect(screen.queryByText(MEMORY_EMPTY_HINT)).toBeNull();
    expect(screen.getByTestId("md-preview").textContent).toBe(nav);
  });

  it("编辑态仍是原文（含退役壳），预览空状态卸掉", async () => {
    const source = makeMemorySource(RETIRED_CHROME);
    await renderEditing(source);

    expect(screen.queryByText(MEMORY_EMPTY_HINT)).toBeNull();
    expect(lastEditorProps.initialDoc).toBe(RETIRED_CHROME);
    expect(source.writeText).not.toHaveBeenCalled();
  });

  it("用户规则 / 工作区 md 不出记忆空状态", async () => {
    const emptyDocs = makeSource({
      id: "documents",
      readForEdit: vi.fn(async () => ({
        text: "",
        version: { etag: "d1" },
        encoding: "utf-8" as const,
        eol: "lf" as const,
      })),
    });
    await renderLoaded(emptyDocs);
    expect(screen.queryByText(MEMORY_EMPTY_HINT)).toBeNull();
    expect(screen.getByTestId("md-preview").textContent).toBe("");

    cleanup();
    const emptyWorkspace = makeSource({
      id: "workspace:cloud",
      readForEdit: vi.fn(async () => ({
        text: "",
        version: { mtimeMs: 1 },
        encoding: "utf-8" as const,
        eol: "lf" as const,
      })),
    });
    await renderLoaded(emptyWorkspace);
    expect(screen.queryByText(MEMORY_EMPTY_HINT)).toBeNull();
  });
});
