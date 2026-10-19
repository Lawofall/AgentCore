// @vitest-environment jsdom
/**
 * Process-tool chrome: folder command surface + export/search/board/notes/git
 * fold to one title line (icon + English verb + chips). Retrieval / inventory
 * counts stay expanded-only. The block comment detaches @vitest-environment
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
    tool: "md_export",
    label: "Export Word",
    args: { path: "报告.md", format: "docx" },
    result: "已导出 Word：报告.docx",
  },
  {
    tool: "md_export",
    label: "Export PDF",
    args: { path: "报告.md", format: "pdf" },
    result: "已导出 PDF：报告.pdf",
  },
  {
    tool: "read",
    label: "Read file",
    args: { path: "shot.png" },
    result: "图里是一块白板",
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

  it("list_folders success does not inline a folder count", () => {
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
    expect(screen.queryByText(/3 folders/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
    fireEvent.click(screen.getByText("List folders"));
    expect(screen.getByText(/共 3 个文件夹/)).toBeTruthy();
  });

  it("search_conversations success does not inline a hit count", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "search_conversations",
          arguments: { query: "部署" },
          result: "1. 上周方案 · abc",
          display: { result_count: 3, scope: "all" },
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Search conversations")).toBeTruthy();
    expect(screen.getByText("部署")).toBeTruthy();
    expect(screen.queryByText(/3 场对话/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
    fireEvent.click(screen.getByText("Search conversations"));
    expect(screen.getByText(/上周方案/)).toBeTruthy();
    expect(screen.queryByText("检索对话")).toBeNull();
    expect(screen.queryByText(/3 场对话/)).toBeNull();
  });

  it("file_list chips directory; '.' stays off the title", () => {
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
    expect(screen.queryByText("类型诊断")).toBeNull();
    expect(screen.getAllByText(/1 个类型错误/)).toHaveLength(1);
  });
});
