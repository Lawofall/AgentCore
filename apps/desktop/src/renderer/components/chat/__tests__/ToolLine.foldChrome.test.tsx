// @vitest-environment jsdom
/**
 * Process-tool chrome: folder command surface + export/search/board/notes/git
 * fold to one title line (icon + English verb + chips). Count meta is inline;
 * results stay expanded-only. The block comment detaches @vitest-environment
 * from the import block so organizeImports keeps it file-leading.
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import type { ProcessStep } from "@/types/events";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: Object.assign(
    (selector: (s: { showBrowser: () => void }) => unknown) =>
      selector({ showBrowser: vi.fn() }),
    { getState: () => ({ showBrowser: vi.fn() }) },
  ),
}));

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}));

import { ToolLine } from "../ToolLine";

afterEach(cleanup);

function renderWithTooltip(ui: ReactElement) {
  return render(<TooltipProvider>{ui}</TooltipProvider>);
}

function collapsedSubline(container: HTMLElement): HTMLElement | null {
  return container.querySelector("span.block.truncate.text-xs");
}

type ToolStep = Extract<ProcessStep, { kind: "tool" }>;

function step(over: Partial<ToolStep>): ToolStep {
  return {
    kind: "tool",
    id: "call_1",
    tool_name: "list_folders",
    arguments: {},
    result: null,
    display: null,
    status: "success",
    ...over,
  };
}

const FOLD_FAMILY: {
  tool: string;
  label: string;
  args: Record<string, unknown>;
  result: string;
  display?: Record<string, unknown> | null;
}[] = [
  {
    tool: "list_folders",
    label: "List folders",
    args: {},
    result: "共 3 个文件夹：\n[...]",
    display: { count: 3 },
  },
  {
    tool: "resolve_folder",
    label: "Resolve folder",
    args: { path: "白板" },
    result: "唯一命中，可直接用于后续派工：\n{...}",
    display: {
      status: "resolved",
      folder_id: "550e8400-e29b-41d4-a716-446655440000",
      name: "白板",
      rel_path: "白板",
      mode: "cloud",
    },
  },
  {
    tool: "create_folder",
    label: "Create folder",
    args: { name: "白板" },
    result: "已创建云文件夹（路径 白板）",
    display: {
      status: "created",
      folder_id: "550e8400-e29b-41d4-a716-446655440000",
      name: "白板",
      rel_path: "白板",
      mode: "cloud",
    },
  },
  {
    tool: "delete_folder",
    label: "Delete folder",
    args: { folder_id: "550e8400-e29b-41d4-a716-446655440000" },
    result: "已删除文件夹「白板」（软删）",
    display: {
      status: "deleted",
      folder_id: "550e8400-e29b-41d4-a716-446655440000",
      name: "白板",
      rel_path: "白板",
    },
  },
  {
    tool: "list_folder_dir",
    label: "List folder dir",
    args: {
      folder_id: "550e8400-e29b-41d4-a716-446655440000",
      directory: "docs",
    },
    result: "f docs/a.md\nf docs/b.md",
  },
  {
    tool: "read_folder_file",
    label: "Read folder file",
    args: {
      folder_id: "550e8400-e29b-41d4-a716-446655440000",
      path: "README.md",
    },
    result: "# 跨文件夹正文不应出现在折叠行",
  },
  {
    tool: "remember",
    label: "Remember",
    args: { content: "以后用中文回复" },
    result: "已记下用户规则。",
  },
  {
    tool: "update_folder_profile",
    label: "Update folder profile",
    args: { content: "## 画像" },
    result: "已写入文件夹画像。",
  },
  {
    tool: "file_batch",
    label: "Batch files",
    args: {},
    result: "已完成 3 项文件操作",
  },
  {
    tool: "md_to_docx",
    label: "Export Word",
    args: { path: "报告.md" },
    result: "已导出 Word：报告.docx",
  },
  {
    tool: "md_to_pdf",
    label: "Export PDF",
    args: { path: "报告.md" },
    result: "已导出 PDF：报告.pdf",
  },
  {
    tool: "archive_extract",
    label: "Extract archive",
    args: { archive: "pkg.zip", dest: "out" },
    result: "已解压 12 个文件",
  },
  {
    tool: "archive_create",
    label: "Create archive",
    args: { sources: ["src"], dest: "pkg.zip" },
    result: "已打包 pkg.zip",
  },
  {
    tool: "download_url",
    label: "Download file",
    args: { url: "https://example.com/a.pdf", path: "a.pdf" },
    result: "已下载 a.pdf",
  },
  {
    tool: "read_image",
    label: "Read image",
    args: { path: "shot.png", prompt: "描述" },
    result: "图里是一块白板",
  },
  {
    tool: "board_ops",
    label: "Edit board",
    args: {},
    result: "已更新白板元素",
  },
  {
    tool: "board_read",
    label: "Read board",
    args: {},
    result: "白板上有三张便利贴",
  },
  {
    tool: "code_search",
    label: "Search code",
    args: { query: "ToolLine" },
    result: "src/ToolLine.tsx:12-40 ToolLine",
  },
  {
    tool: "git",
    label: "Git",
    args: { subcommand: "status" },
    result: "On branch main\nnothing to commit",
  },
  {
    tool: "code_diagnostics",
    label: "Check types",
    args: { paths: ["a.ts"] },
    result: "2 个类型错误",
    display: {
      kind: "code_diagnostics",
      status: "ok",
      diagnostics: [
        {
          path: "a.ts",
          line: 1,
          column: 1,
          severity: "error",
          message: "boom",
        },
      ],
    },
  },
  {
    tool: "external_mount_readonly",
    label: "Mount folder",
    args: { path: "C:\\\\data" },
    result: "已挂载只读目录",
  },
  {
    tool: "delegate",
    label: "Delegate",
    args: { goal: "调研白板" },
    result: "已派出 研究员（白板桌）。",
  },
];

describe("ToolLine · 过程工具折叠一行", () => {
  it.each(FOLD_FAMILY)(
    "folds $tool to one line without snake_case or text-xs peek",
    ({ tool, label, args, result, display }) => {
      const { container } = renderWithTooltip(
        <ToolLine
          step={step({
            tool_name: tool,
            arguments: args,
            result,
            display: display ?? null,
            status: "success",
          })}
        />,
      );
      expect(screen.getByText(label)).toBeTruthy();
      expect(screen.queryByText(tool)).toBeNull();
      expect(collapsedSubline(container)).toBeNull();
      expect(container.textContent).not.toContain(
        result.split("\n")[0] ?? result,
      );
    },
  );

  it("list_folders success inlineMeta shows N folders", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "list_folders",
          result: "共 3 个文件夹：\n[...]",
          display: { count: 3 },
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("List folders")).toBeTruthy();
    expect(screen.getByText(/3 folders/)).toBeTruthy();
    expect(screen.getByText(/3 folders/).className).toMatch(/max-w-\[40%\]/);
    expect(collapsedSubline(container)).toBeNull();
    fireEvent.click(screen.getByText("List folders"));
    expect(screen.getByText(/共 3 个文件夹/)).toBeTruthy();
  });

  it("list_folders 1 folder singular inlineMeta", () => {
    renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "list_folders",
          result: "共 1 个文件夹：",
          display: { count: 1 },
          status: "success",
        })}
      />,
    );
    expect(screen.getByText(/1 folder$/)).toBeTruthy();
  });

  it("file_list / list_folder_dir chip directory; '.' stays off the title", () => {
    const { rerender, container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "file_list",
          arguments: { directory: "src/app" },
          result: "f src/app/a.ts",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("List dir")).toBeTruthy();
    expect(screen.getByText("src/app")).toBeTruthy();
    expect(collapsedSubline(container)).toBeNull();

    rerender(
      <TooltipProvider>
        <ToolLine
          step={step({
            id: "call_2",
            tool_name: "file_list",
            arguments: { directory: "." },
            result: "f README.md",
            status: "success",
          })}
        />
      </TooltipProvider>,
    );
    expect(screen.getByText("List dir")).toBeTruthy();
    expect(screen.queryByText(/^\.$/)).toBeNull();

    rerender(
      <TooltipProvider>
        <ToolLine
          step={step({
            id: "call_3",
            tool_name: "list_folder_dir",
            arguments: {
              folder_id: "550e8400-e29b-41d4-a716-446655440000",
              directory: "docs",
            },
            result: "f docs/a.md",
            status: "success",
          })}
        />
      </TooltipProvider>,
    );
    expect(screen.getByText("List folder dir")).toBeTruthy();
    expect(screen.getByText("docs")).toBeTruthy();
    expect(
      screen.queryByText("550e8400-e29b-41d4-a716-446655440000"),
    ).toBeNull();
  });

  it("git title chip is subcommand, not the commit headline", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "git",
          arguments: {
            subcommand: "commit",
            message: "feat: fold process tools into one line",
          },
          result: "[main abc1234] feat: fold process tools into one line",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Git")).toBeTruthy();
    expect(screen.getByText("commit")).toBeTruthy();
    expect(screen.queryByText(/feat: fold process tools/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("resolve/create/delete_folder display.name is not consult chrome", () => {
    for (const over of [
      {
        tool_name: "resolve_folder",
        label: "Resolve folder",
        arguments: { path: "白板" },
        result: "唯一命中，可直接用于后续派工",
        display: {
          status: "resolved",
          folder_id: "550e8400-e29b-41d4-a716-446655440000",
          name: "白板",
          rel_path: "白板",
        },
      },
      {
        tool_name: "create_folder",
        label: "Create folder",
        arguments: { name: "白板" },
        result: "已创建云文件夹",
        display: {
          status: "created",
          folder_id: "550e8400-e29b-41d4-a716-446655440000",
          name: "白板",
          rel_path: "白板",
        },
      },
      {
        tool_name: "delete_folder",
        label: "Delete folder",
        arguments: { folder_id: "550e8400-e29b-41d4-a716-446655440000" },
        result: "已删除文件夹「白板」",
        display: {
          status: "deleted",
          folder_id: "550e8400-e29b-41d4-a716-446655440000",
          name: "白板",
          rel_path: "白板",
        },
      },
    ] as const) {
      const { container, unmount } = renderWithTooltip(
        <ToolLine
          step={step({
            ...over,
            status: "success",
          })}
        />,
      );
      expect(screen.getByText(over.label)).toBeTruthy();
      expect(screen.queryByText("Consult")).toBeNull();
      expect(collapsedSubline(container)).toBeNull();
      fireEvent.click(screen.getByText(over.label));
      expect(screen.queryByText("查阅记忆：")).toBeNull();
      expect(screen.queryByText("查阅记忆")).toBeNull();
      expect(screen.getByText(over.result)).toBeTruthy();
      unmount();
    }
  });

  it("code_diagnostics diagnostic summary lands in title inlineMeta", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "code_diagnostics",
          arguments: { paths: ["a.ts"] },
          result: "1 个类型错误\na.ts:1:1 boom",
          display: {
            kind: "code_diagnostics",
            status: "ok",
            diagnostics: [
              {
                path: "a.ts",
                line: 1,
                column: 1,
                severity: "error",
                message: "boom",
              },
            ],
          },
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Check types")).toBeTruthy();
    expect(screen.getByText(/1 个类型错误/)).toBeTruthy();
    expect(collapsedSubline(container)).toBeNull();
    fireEvent.click(screen.getByText("Check types"));
    expect(screen.getByText("boom")).toBeTruthy();
  });
});
