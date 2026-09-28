// @vitest-environment jsdom
/**
 * Render test for ToolLine 过程工具默认折叠: every process tool (web_search / code_execute /
 * write / edit / …) stays collapsed on the running→done edge — aligned with
 * Cursor/Claude「过程收敛、答案突出」. Folded rows keep inlineMeta / inlineBody /
 * peek; expand is a click away. Failures stay collapsed (red ✗, one line);
 * specific product copy lives in the expanded detail.
 * The block comment detaches the
 * @vitest-environment directive from the import block so organizeImports keeps it file-leading.
 */

import { GENERIC_TOOL_FAILURE_MESSAGE } from "@/components/chat/toolResult/productFailureFace";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { ProcessStep } from "@/types/events";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const showBrowser = vi.fn();
const navigate = vi.fn();
vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: Object.assign(
    (selector: (s: { showBrowser: typeof showBrowser }) => unknown) =>
      selector({ showBrowser }),
    { getState: () => ({ showBrowser }) },
  ),
}));

vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useNavigate: () => navigate,
  };
});

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}));

import { useToolOutputLiveStore } from "@/stores/toolOutputLive";
import { ComposingToolLine, ToolLine, ToolLineGroup } from "../ToolLine";
import { toolDetail, toolGroupSummary } from "../message-bubble/constants";

afterEach(() => {
  cleanup();
  useToolOutputLiveStore.setState({ byId: {}, selectedId: null });
});

beforeEach(() => {
  showBrowser.mockReset();
  navigate.mockReset();
});

function renderWithTooltip(ui: ReactElement) {
  return render(<TooltipProvider>{ui}</TooltipProvider>);
}

/** Collapsed result subline (`text-xs` under the title). Null when the row is one line. */
function collapsedSubline(container: HTMLElement): HTMLElement | null {
  return container.querySelector("span.block.truncate.text-xs");
}

/** Former web_search running silhouette (three fake result cards). Must stay gone. */
function searchResultSilhouette(container: HTMLElement): HTMLElement | null {
  return container.querySelector('[aria-hidden][class*="mt-1"]');
}

type ToolStep = Extract<ProcessStep, { kind: "tool" }>;

function step(over: Partial<ToolStep>): ToolStep {
  return {
    kind: "tool",
    id: "call_1",
    tool_name: "code_execute",
    arguments: {},
    result: null,
    display: null,
    status: "success",
    ...over,
  };
}

function readUrlStep(
  id: string,
  over: {
    url: string;
    title: string;
    site: string;
    snippet?: string;
    content?: string;
    status?: ToolStep["status"];
  },
): ToolStep {
  return step({
    id,
    tool_name: "web_fetch",
    arguments: { url: over.url },
    result: "ok",
    display: {
      url: over.url,
      title: over.title,
      site: over.site,
      snippet: over.snippet,
      content: over.content ?? "正文不应出现在合并态",
    },
    status: over.status ?? "success",
  });
}

describe("ToolLine · 过程工具默认折叠", () => {
  it("keeps code_execute's terminal collapsed on the running→done edge", () => {
    const { rerender, container } = render(
      <ToolLine
        step={step({
          tool_name: "code_execute",
          arguments: { code: "print('hi')", language: "python" },
          status: "running",
        })}
      />,
    );
    // Running: nothing to expand yet — no result face on the tail.
    expect(screen.queryByTestId("tool-fault-label")).toBeNull();
    expect(screen.queryByText(/退出码 0/)).toBeNull();

    rerender(
      <ToolLine
        step={step({
          tool_name: "code_execute",
          arguments: { code: "print('hi')", language: "python" },
          result: "stdout:\nhello world",
          display: {
            stdout: "hello world\n",
            stderr: "",
            exit_code: 0,
            language: "python",
          },
          status: "success",
        })}
      />,
    );
    // Done: one line — language in title, stdout in expand. Success hangs no mark.
    expect(screen.getByText("python")).toBeTruthy();
    expect(screen.queryByText(/hello world/)).toBeNull();
    expect(screen.queryByText(/退出码 0/)).toBeNull();
    expect(screen.queryByTestId("tool-fault-label")).toBeNull();
    expect(collapsedSubline(container)).toBeNull();

    fireEvent.click(screen.getByText("Run code"));
    expect(screen.getByText(/hello world/)).toBeTruthy();
    expect(screen.queryByText(/退出码 0/)).toBeNull();
    expect(screen.queryByTestId("tool-fault-label")).toBeNull();
    expect(screen.getAllByText("python")).toHaveLength(1);
  });

  it("keeps a running web_search to one title line (no result skeleton)", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "web_search",
          arguments: { query: "深圳天气" },
          status: "running",
        })}
      />,
    );
    expect(screen.getByText("Search web")).toBeTruthy();
    expect(screen.getByText("深圳天气")).toBeTruthy();
    expect(searchResultSilhouette(container)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("keeps web_search results collapsed on completion", () => {
    const { rerender, container } = render(
      <ToolLine
        step={step({
          tool_name: "web_search",
          arguments: { query: "深圳天气" },
          status: "running",
        })}
      />,
    );
    expect(screen.queryByText("深圳天气预报")).toBeNull();

    rerender(
      <ToolLine
        step={step({
          tool_name: "web_search",
          arguments: { query: "深圳天气" },
          result: "1 result",
          display: {
            query: "深圳天气",
            results: [
              {
                title: "深圳天气预报",
                url: "https://w.example.com",
                site: "w.example.com",
                snippet: "多云转晴",
              },
            ],
          },
          status: "success",
        })}
      />,
    );
    expect(screen.queryByText("深圳天气预报")).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
    fireEvent.click(screen.getByText("Search web"));
    expect(screen.getByText("深圳天气预报")).toBeTruthy();
    expect(screen.getByText(/· w\.example\.com/)).toBeTruthy();
    expect(screen.getByText("多云转晴")).toBeTruthy();
    expect(screen.queryByText(/搜索：/)).toBeNull();
  });

  it("does not inline web_search result count into the title row when collapsed", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "web_search",
          arguments: { query: "深圳天气" },
          result: "1 result",
          display: {
            query: "深圳天气",
            results: [
              {
                title: "深圳天气预报",
                url: "https://w.example.com",
                site: "w.example.com",
                snippet: "多云转晴",
              },
            ],
          },
          status: "success",
        })}
      />,
    );
    expect(screen.queryByText(/1 result/)).toBeNull();
    expect(screen.queryByText("深圳天气预报")).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("does not inline web_search count on nested rows either", () => {
    const { container } = render(
      <ToolLine
        nested
        step={step({
          tool_name: "web_search",
          arguments: { query: "深圳天气" },
          result: "5 results",
          display: {
            query: "深圳天气",
            results: Array.from({ length: 5 }, (_, i) => ({
              title: `hit ${i + 1}`,
              url: `https://w.example.com/${i}`,
              site: "w.example.com",
              snippet: "snippet",
            })),
          },
          status: "success",
        })}
      />,
    );
    expect(screen.queryByText(/5 results/)).toBeNull();
    expect(screen.queryByText("hit 1")).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("inlines edit +/- into the title and keeps the diff collapsed", () => {
    const { rerender, container } = render(
      <ToolLine
        step={step({
          tool_name: "edit",
          arguments: {
            file_path: "src/foo.ts",
            old_string: "const x = 1",
            new_string: "const x = 2",
          },
          status: "running",
        })}
      />,
    );
    expect(screen.queryByText("+1")).toBeNull();

    rerender(
      <ToolLine
        step={step({
          tool_name: "edit",
          arguments: {
            file_path: "src/foo.ts",
            old_string: "const x = 1",
            new_string: "const x = 2",
          },
          result: "已编辑 src/foo.ts",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("+1")).toBeTruthy();
    expect(screen.getByText("-1")).toBeTruthy();
    expect(screen.queryByText(/已编辑/)).toBeNull();
    expect(screen.getByText("src/foo.ts")).toBeTruthy();
    expect(collapsedSubline(container)).toBeNull();

    fireEvent.click(screen.getByText("Edit file"));
    expect(screen.getAllByText("+1")).toHaveLength(1);
    expect(screen.getAllByText("-1")).toHaveLength(1);
    expect(screen.getAllByText("src/foo.ts")).toHaveLength(1);
  });

  it("omits the zero side of edit +/- on the title", () => {
    const { rerender } = render(
      <ToolLine
        step={step({
          tool_name: "edit",
          arguments: {
            file_path: "src/foo.ts",
            old_string: "a\nc",
            new_string: "a\nb\nc",
          },
          result: "已编辑 src/foo.ts",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("+1")).toBeTruthy();
    expect(screen.queryByText("-0")).toBeNull();

    rerender(
      <ToolLine
        step={step({
          tool_name: "edit",
          arguments: {
            file_path: "src/foo.ts",
            old_string: "a\nb\nc",
            new_string: "a\nc",
          },
          result: "已编辑 src/foo.ts",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("-1")).toBeTruthy();
    expect(screen.queryByText("+0")).toBeNull();
    expect(screen.queryByText("+1")).toBeNull();
  });

  it("inlines write line count into the title and keeps the card collapsed", () => {
    const { rerender, container } = render(
      <ToolLine
        step={step({
          tool_name: "write",
          arguments: { file_path: "src/new.ts", content: "export const x = 1" },
          status: "running",
        })}
      />,
    );
    expect(screen.queryByText(/1 行/)).toBeNull();

    rerender(
      <ToolLine
        step={step({
          tool_name: "write",
          arguments: { file_path: "src/new.ts", content: "export const x = 1" },
          result: "已写入 src/new.ts",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText(/1 行/)).toBeTruthy();
    expect(screen.queryByText(/字/)).toBeNull();
    expect(screen.queryByText(/已写入/)).toBeNull();
    expect(screen.getByText("src/new.ts")).toBeTruthy();
    expect(collapsedSubline(container)).toBeNull();

    fireEvent.click(screen.getByText("Write file"));
    expect(screen.getAllByText(/1 行/)).toHaveLength(1);
    expect(screen.getAllByText("src/new.ts")).toHaveLength(1);
    expect(screen.queryByText(/字/)).toBeNull();
  });

  it("inlines write diagnostics into the title and stays one line", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "write",
          arguments: { file_path: "src/new.ts", content: "export const x = 1" },
          result: "已写入 src/new.ts",
          display: {
            kind: "code_diagnostics",
            status: "ok",
            diagnostics: [
              {
                path: "src/new.ts",
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
    expect(screen.getByText(/1 个类型错误/)).toBeTruthy();
    expect(screen.getByText(/1 行/)).toBeTruthy();
    expect(screen.queryByText(/已写入/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("suppresses write ack peek — title path is enough", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "write",
          arguments: { file_path: "notes.md", content: "tail" },
          result: "已写入 notes.md",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Write file")).toBeTruthy();
    expect(screen.getByText("notes.md")).toBeTruthy();
    expect(screen.queryByText(/已写入/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("suppresses grep hit counts — title pattern is enough", () => {
    const { container, rerender } = render(
      <ToolLine
        step={step({
          tool_name: "grep",
          arguments: { pattern: "include_usage|stream_options" },
          result:
            "1 处匹配，分布在 1 个文件中（/include_usage|stream_options/）\nsrc/a.ts:1: include_usage",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Grep code")).toBeTruthy();
    expect(screen.getByText("include_usage|stream_options")).toBeTruthy();
    expect(screen.queryByText(/处匹配/)).toBeNull();
    expect(screen.queryByText(/个文件/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();

    rerender(
      <ToolLine
        step={step({
          tool_name: "grep",
          arguments: { pattern: "foo" },
          result: "3 个文件匹配 /foo/\na.ts: 2",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("foo")).toBeTruthy();
    expect(screen.queryByText(/个文件/)).toBeNull();

    rerender(
      <ToolLine
        step={step({
          tool_name: "grep",
          arguments: { pattern: "Nope" },
          result:
            "本次 grep 未匹配 /Nope/。不要据此断定代码不存在。可执行下一步：① 收窄",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Nope")).toBeTruthy();
    expect(screen.queryByText(/未匹配/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("keeps a long grep pattern from overflowing the title row", () => {
    const pattern =
      "<(article|ProcessLane|TeamLane|SourceCards|InteractionLane|Collapsible|collapsed|折叠|展开|useState|open|sumr)";
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "grep",
          arguments: { pattern },
          result: `${pattern}\nsrc/ChatView.tsx:54: hit`,
          status: "success",
        })}
      />,
    );
    const btn = container.querySelector("button");
    expect(btn?.className).toMatch(/min-w-0/);
    expect(btn?.className).toMatch(/overflow-hidden/);
    expect(screen.getByText("Grep code")).toBeTruthy();
    // Unknown grep shape must not re-attach the pattern tail as inlineMeta.
    expect(screen.queryByText(/折叠/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("suppresses the peek for consult_memory — only the self-sufficient title shows", () => {
    const { rerender } = render(
      <ToolLine
        step={step({
          tool_name: "consult_memory",
          arguments: { name: "部署流程" },
          status: "running",
        })}
      />,
    );
    rerender(
      <ToolLine
        step={step({
          tool_name: "consult_memory",
          arguments: { name: "部署流程" },
          result: "用 pnpm dev 起前端",
          display: { topic: "部署流程" },
          status: "success",
        })}
      />,
    );
    // 查阅类工具的标题已自解释（查阅记忆 部署流程）、正文一键即达 → 折叠态不再另起 peek 行。
    // The note body (expanded-only) never renders, and the topic shows exactly once (the title
    // detail — no duplicate peek line echoing it).
    expect(screen.queryByText(/用 pnpm dev 起前端/)).toBeNull();
    expect(screen.getAllByText("部署流程")).toHaveLength(1);
  });

  it("suppresses the peek for unified consult — same as consult_memory", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "consult",
          arguments: { name: "部署流程" },
          result: "用 pnpm dev 起前端",
          display: { name: "部署流程", origin: "user" },
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Consult")).toBeTruthy();
    expect(container.querySelector(".lucide-brain")).toBeNull();
    expect(container.querySelector(".lucide-book-open")).toBeTruthy();
    expect(screen.queryByText(/用 pnpm dev 起前端/)).toBeNull();
    expect(screen.getAllByText("部署流程")).toHaveLength(1);
    fireEvent.click(screen.getByText("Consult"));
    expect(screen.getByText(/用 pnpm dev 起前端/)).toBeTruthy();
    expect(screen.getAllByText("部署流程")).toHaveLength(1);
    expect(screen.queryByText("设定")).toBeNull();
  });

  it("read_conversation 折叠态亮对话标题（不摆 conversation_id、不泄正文）", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "read_conversation",
          arguments: { conversation_id: "conv_abc" },
          result: "### User\n很长的 transcript 正文",
          display: {
            title: "上周方案",
            conversation_id: "conv_abc",
            truncated: false,
          },
          status: "success",
        })}
      />,
    );
    expect(screen.queryByText(/很长的 transcript/)).toBeNull();
    expect(screen.queryByText("conv_abc")).toBeNull();
    expect(container.textContent).toContain("上周方案");
    expect(screen.getByRole("button", { name: "打开" })).toBeTruthy();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("read_conversation 未读完时标题写「截断」，行尾「打开」不展开正文", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "read_conversation",
          arguments: { conversation_id: "conv_abc", query: "上周方案" },
          result: "### User\n很长的 transcript 正文",
          display: {
            title: "法庭迷局游戏设计",
            conversation_id: "conv_abc",
            truncated: true,
          },
          status: "success",
        })}
      />,
    );
    expect(container.textContent).toContain("法庭迷局游戏设计 · 截断");
    expect(collapsedSubline(container)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "打开" }));
    expect(navigate).toHaveBeenCalledWith("/conversations/conv_abc");
    expect(screen.queryByText(/很长的 transcript/)).toBeNull();
    fireEvent.click(screen.getByText("Read conversation"));
    expect(screen.getByText(/很长的 transcript/)).toBeTruthy();
    expect(navigate).toHaveBeenCalledTimes(1);
  });

  it("suppresses the peek for consult_skill — the summary shows only when expanded", () => {
    render(
      <ToolLine
        step={step({
          tool_name: "consult_skill",
          arguments: { name: "debate_and_review" },
          result: "完整能力指引正文…",
          display: {
            skill_name: "debate_and_review",
            summary: "对需对抗性多视角思考的问题用 debate 工具发起结构化辩论",
          },
          status: "success",
        })}
      />,
    );
    // 折叠态只留标题（查阅能力 debate_and_review）——summary 不再作为 peek 行出现，展开卡片里才有。
    expect(screen.getByText("debate_and_review")).toBeTruthy();
    expect(
      screen.queryByText(/对需对抗性多视角思考的问题用 debate 工具/),
    ).toBeNull();
    fireEvent.click(screen.getByText("Consult skill"));
    expect(
      screen.getByText(/对需对抗性多视角思考的问题用 debate 工具/),
    ).toBeTruthy();
    expect(screen.getAllByText("debate_and_review")).toHaveLength(1);
    expect(screen.queryByText("能力指引")).toBeNull();
  });

  it("keeps update_synthesis draft out of the title and suppresses the ack peek", () => {
    const draft = [
      "## 进展简报",
      "**当前状态**: 法律分析已完成",
      "| 队员 | 状态 |",
      "| --- | --- |",
      "| 法律分析 | ✅ 完成 |",
    ].join("\n");
    const ack = "已更新合成草稿（341 字），用户可见「进展中」预览。";
    render(
      <ToolLine
        step={step({
          tool_name: "update_synthesis",
          arguments: { draft },
          result: ack,
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Update synthesis")).toBeTruthy();
    expect(screen.queryByText(/进展简报/)).toBeNull();
    expect(screen.queryByText(/法律分析已完成/)).toBeNull();
    // 折叠态：协调 ack 不再作 peek；展开后才见结果正文。
    expect(screen.queryByText(ack)).toBeNull();
    fireEvent.click(screen.getByText("Update synthesis"));
    expect(screen.getByText(ack)).toBeTruthy();
  });

  it("suppresses read / file_list result-first-line peeks", () => {
    const { rerender } = render(
      <ToolLine
        step={step({
          tool_name: "read",
          arguments: { file_path: "lv_jasmine_report/lv_jasmine_synthesis.md" },
          result: "# LV诉茉莉奶白案：四路分析交叉验证与综合研判\n\n正文…",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Read file")).toBeTruthy();
    expect(
      screen.getByText("lv_jasmine_report/lv_jasmine_synthesis.md"),
    ).toBeTruthy();
    expect(screen.queryByText(/四路分析交叉验证/)).toBeNull();

    rerender(
      <ToolLine
        step={step({
          id: "call_2",
          tool_name: "file_list",
          arguments: { path: "lv_jasmine_report" },
          result: "f lv_jasmine_report/lv_jasmine_cultural.md\nf other.md",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("List dir")).toBeTruthy();
    expect(screen.getByText("lv_jasmine_report")).toBeTruthy();
    expect(screen.queryByText(/lv_jasmine_cultural/)).toBeNull();
  });

  it("inlines a read window into the title and strips the footer when expanded", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "read",
          arguments: { file_path: "src/ui/PropertyPanel.tsx" },
          result: "191| const x = 1\n\n（第 1–200 行，共 242 行）",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Read file")).toBeTruthy();
    expect(screen.getByText("src/ui/PropertyPanel.tsx")).toBeTruthy();
    expect(screen.getByText(/1–200 行/)).toBeTruthy();
    expect(screen.queryByText(/\/ 242/)).toBeNull();
    expect(screen.queryByText(/第 1/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();

    fireEvent.click(screen.getByText("Read file"));
    expect(screen.getAllByText(/1–200 行/)).toHaveLength(1);
    expect(screen.getByText(/191\| const x = 1/)).toBeTruthy();
    expect(screen.queryByText(/共 242 行/)).toBeNull();
  });

  it("does not hang a full-file line count on a read title", () => {
    render(
      <ToolLine
        step={step({
          tool_name: "read",
          arguments: { file_path: "src/ui/PropertyPanel.tsx" },
          result: "const x = 1\n\n（全文 12 行）",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("src/ui/PropertyPanel.tsx")).toBeTruthy();
    expect(screen.queryByText(/12 行/)).toBeNull();
    expect(screen.queryByText(/全文/)).toBeNull();

    fireEvent.click(screen.getByText("Read file"));
    expect(screen.getByText("const x = 1")).toBeTruthy();
    expect(screen.queryByText(/全文 12 行/)).toBeNull();
  });

  it("omits the green check on nested success rows", () => {
    const { container } = render(
      <ToolLine
        nested
        step={step({
          tool_name: "read",
          arguments: { file_path: "docs/a.md" },
          result: "ok",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Read file")).toBeTruthy();
    expect(container.querySelector(".lucide-check")).toBeNull();
  });

  it("chips the person replan tell addresses without dumping the note", () => {
    const ack = "已把话传给 run_legal_1，他会按这句继续。";
    render(
      <ToolLine
        step={step({
          tool_name: "replan",
          arguments: {
            tell: [
              {
                run_id: "run_legal_1",
                note: "请按公司法 §20 继续，详细论述如下……\n第二段。",
              },
            ],
          },
          result: ack,
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Replan")).toBeTruthy();
    expect(screen.getByText("run_legal_1")).toBeTruthy();
    expect(screen.queryByText(/请按公司法/)).toBeNull();
    expect(screen.queryByText(ack)).toBeNull();
  });

  it("leaves web_fetch collapsed on completion (same default as every other tool)", () => {
    const { rerender, container } = render(
      <ToolLine
        step={step({
          tool_name: "web_fetch",
          arguments: { url: "https://weather.example.com/sz" },
          status: "running",
        })}
      />,
    );
    rerender(
      <ToolLine
        step={step({
          tool_name: "web_fetch",
          arguments: { url: "https://weather.example.com/sz" },
          result: '{"url":"…","title":"深圳天气","content":"正文"}',
          display: {
            url: "https://weather.example.com/sz",
            title: "深圳天气",
            site: "weather.example.com",
            snippet: "多云",
            content: "正文预览不应自动展开",
          },
          status: "success",
        })}
      />,
    );
    // inlineMeta shows「标题 · 域名」; body stays hidden until the user expands.
    expect(container.textContent).toContain("深圳天气 · weather.example.com");
    expect(screen.queryByText(/正文预览不应自动展开/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });
});

describe("ToolLine · browser 单步折叠一行", () => {
  it("inlines click page identity, not 点击元素 ref", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "browser_click",
          arguments: { ref: "e13" },
          result: "ok",
          display: {
            kind: "browser",
            action: "click",
            url: "https://example.com",
            title: "示例首页",
            detail: "点击元素 e13",
          },
          status: "success",
        })}
      />,
    );
    expect(screen.getAllByText("Click")).toHaveLength(1);
    expect(screen.getByText(/示例首页/)).toBeTruthy();
    expect(screen.queryByText(/点击元素 e13/)).toBeNull();
    expect(screen.queryByText("e13")).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("inlines snapshot page title, not 读取页面结构 version", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "browser",
          arguments: { action: "snapshot" },
          result: "ok",
          display: {
            kind: "browser",
            action: "snapshot",
            url: "https://example.com",
            title: "示例首页",
            detail: "读取页面结构（v2）",
          },
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Snapshot")).toBeTruthy();
    expect(screen.getByText(/示例首页/)).toBeTruthy();
    expect(screen.queryByText(/读取页面结构/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("does not restate Screenshot as 截取当前页面", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "browser",
          arguments: { action: "screenshot" },
          result: "ok",
          display: {
            kind: "browser",
            action: "screenshot",
            url: "https://example.com",
            title: "示例首页",
            detail: "截取当前页面",
          },
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Screenshot")).toBeTruthy();
    expect(screen.getByText(/示例首页/)).toBeTruthy();
    expect(screen.queryByText(/截取当前页面/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("inlines navigate page title, not 打开-url detail", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "browser_navigate",
          arguments: { url: "https://example.com" },
          result: "ok",
          display: {
            kind: "browser",
            action: "navigate",
            url: "https://example.com",
            title: "示例首页",
            detail: "打开 https://example.com",
          },
          status: "success",
        })}
        conversationId="c1"
      />,
    );
    expect(screen.getByText("Navigate")).toBeTruthy();
    expect(screen.getByText(/示例首页/)).toBeTruthy();
    expect(screen.queryByText(/打开 https:\/\/example.com/)).toBeNull();
    expect(screen.queryByText("打开浏览器")).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
    fireEvent.click(screen.getByText("Navigate"));
    expect(screen.getByText("https://example.com")).toBeTruthy();
  });

  it("falls back to url when navigate has no page title", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "browser_navigate",
          arguments: { url: "https://example.com" },
          result: "ok",
          display: {
            kind: "browser",
            action: "navigate",
            url: "https://example.com",
            detail: "打开 https://example.com",
          },
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Navigate")).toBeTruthy();
    expect(screen.getByText(/https:\/\/example.com/)).toBeTruthy();
    expect(screen.queryByText(/打开 https:\/\/example.com/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("shows a running run's command and live output, with no empty well", () => {
    useToolOutputLiveStore.setState({
      byId: {
        call_1: {
          toolCallId: "call_1",
          toolName: "run",
          conversationId: "c1",
          startedAt: "2026-01-01T00:00:00.000Z",
          stdout: "hello from run",
          stderr: "",
        },
      },
      selectedId: null,
    });
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "run",
          arguments: { command: "pnpm test" },
          status: "running",
          result: null,
        })}
      />,
    );
    expect(screen.getByText("Run")).toBeTruthy();
    expect(screen.getByText("pnpm test")).toBeTruthy();
    expect(screen.getByText("hello from run")).toBeTruthy();
    expect(container.querySelector(".lucide-chevron-down")).toBeTruthy();
    expect(screen.queryByText("（无输出）")).toBeNull();
    fireEvent.click(screen.getByText("Run"));
    expect(screen.queryByText("pnpm test")).toBeNull();
    expect(screen.queryByText("hello from run")).toBeNull();
  });

  it("keeps a running click as a single title line", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "browser_click",
          arguments: { ref: "e13" },
          status: "running",
          result: null,
        })}
      />,
    );
    expect(screen.getByText("Click")).toBeTruthy();
    expect(screen.queryByText("e13")).toBeNull();
    expect(screen.queryByText(/点击元素/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("does not show live elapsed on a running tool", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "grep",
          arguments: { pattern: "WaveScheduler" },
          status: "running",
          result: null,
        })}
      />,
    );
    expect(screen.getByText("WaveScheduler")).toBeTruthy();
    expect(screen.queryByText("6s")).toBeNull();
    expect(screen.queryByText("1m 30s")).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
    expect(container.querySelector("[data-live-flow]")).not.toBeNull();
  });

  it("keeps the collapsed error row to one line (no failure.message subline)", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "browser_click",
          arguments: { ref: "e13" },
          result: "ElementNotFound: e13",
          status: "error",
          failure: { message: "未找到元素 e13。", code: "NOT_FOUND" },
        })}
      />,
    );
    expect(screen.getByText("Click")).toBeTruthy();
    expect(screen.queryByTestId("tool-fault-label")).toBeNull();
    expect(screen.queryByText("未找到")).toBeNull();
    expect(screen.queryByText("未找到元素 e13。")).toBeNull();
    expect(screen.queryByText(/ElementNotFound/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });
});

describe("ToolLine · live-flow", () => {
  it("sweeps a running read row", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "read",
          arguments: { file_path: "src/foo.ts" },
          status: "running",
          result: null,
        })}
      />,
    );
    expect(container.querySelector("[data-live-flow]")).not.toBeNull();
    expect(container.querySelector(".live-flow-text")).not.toBeNull();
  });

  it("sweeps a running web_search title without a result silhouette", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "web_search",
          arguments: { query: "深圳天气" },
          status: "running",
          result: null,
        })}
      />,
    );
    expect(container.querySelector("[data-live-flow]")).not.toBeNull();
    expect(searchResultSilhouette(container)).toBeNull();
    expect(container.querySelector(".mt-1 .bg-muted")).toBeNull();
  });

  it("does not sweep a settled tool row", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "read",
          arguments: { file_path: "src/foo.ts" },
          status: "success",
          result: "ok",
        })}
      />,
    );
    expect(container.querySelector("[data-live-flow]")).toBeNull();
  });
});

describe("ToolLineGroup · live-flow", () => {
  const groupTools = [
    step({
      id: "a",
      tool_name: "read",
      arguments: { file_path: "src/foo.ts" },
      status: "success",
      result: "ok",
    }),
    step({
      id: "b",
      tool_name: "read",
      arguments: { file_path: "src/bar.ts" },
      status: "running",
      result: null,
    }),
  ];

  it("sweeps the collapsed header while a child is running", () => {
    const { container } = render(
      <ToolLineGroup tools={groupTools} isStreaming={false} />,
    );
    expect(container.querySelectorAll("[data-live-flow]")).toHaveLength(1);
    expect(screen.getByText(/foo\.ts · bar\.ts/)).toBeTruthy();
    expect(container.querySelector(".lucide-chevron-right")).toBeTruthy();
  });

  it("does not show live elapsed on the collapsed header", () => {
    render(<ToolLineGroup tools={groupTools} isStreaming={false} />);
    expect(screen.queryByText("6s")).toBeNull();
    expect(screen.queryByText("1m 30s")).toBeNull();
  });

  it("sweeps the running child instead of the header while expanded", () => {
    const { container } = render(
      <ToolLineGroup tools={groupTools} isStreaming />,
    );
    expect(screen.getByText("src/bar.ts")).toBeTruthy();
    expect(container.querySelectorAll("[data-live-flow]")).toHaveLength(1);
  });
});

describe("ToolLineGroup · web_fetch 来源集合", () => {
  const sources = [
    readUrlStep("r1", {
      url: "https://zhuanlan.zhihu.com/p/1050596771_121124370",
      title: "相对论入门",
      site: "zhuanlan.zhihu.com",
      snippet: "时空弯曲简介",
    }),
    readUrlStep("r2", {
      url: "https://baike.baidu.com/item/相对论",
      title: "相对论_百度百科",
      site: "baike.baidu.com",
      snippet: "物理学理论",
    }),
  ];

  it("merges ≥2 web_fetch into a count-title header without collapsed pills", () => {
    renderWithTooltip(<ToolLineGroup tools={sources} isStreaming={false} />);
    const header = screen.getByRole("button", {
      name: /Read page · 2 sources/,
    });
    expect(header).toBeTruthy();
    // Button primitive defaults to font-medium; process headers override to
    // font-normal (ToolLine / ThinkingHeader / DefaultToolLineGroup).
    expect(header.className).toMatch(/font-normal/);
    // 折叠态收敛为纯标题行（对齐工具组 / 思考过程）——来源 pills 移到展开态，不再平铺。
    expect(screen.queryByText("zhuanlan.zhihu.com")).toBeNull();
    expect(screen.queryByText("baike.baidu.com")).toBeNull();
    // Merged view does not inline page bodies.
    expect(screen.queryByText(/正文不应出现在合并态/)).toBeNull();
  });

  it("does not hang 未找到 on the collapsed source-collection header", () => {
    renderWithTooltip(
      <ToolLineGroup
        tools={[
          sources[0],
          readUrlStep("r-fail", {
            url: "https://missing.example.com/x",
            title: "",
            site: "missing.example.com",
            status: "error",
          }),
        ]}
        isStreaming={false}
      />,
    );
    expect(screen.getByText("Read page · 2 sources")).toBeTruthy();
    expect(screen.queryByTestId("tool-group-fault")).toBeNull();
    expect(screen.queryByText("未找到")).toBeNull();
  });

  it("expands to search-style title · domain rows with snippet, without body", () => {
    renderWithTooltip(<ToolLineGroup tools={sources} isStreaming={false} />);
    fireEvent.click(screen.getByText("Read page · 2 sources"));
    expect(screen.getByText("相对论入门")).toBeTruthy();
    expect(screen.getByText("相对论")).toBeTruthy(); // cleanSourceTitle strips _百度百科
    expect(screen.getByText(/· zhuanlan\.zhihu\.com/)).toBeTruthy();
    expect(screen.getByText(/· baike\.baidu\.com/)).toBeTruthy();
    expect(screen.getByText("时空弯曲简介")).toBeTruthy();
    expect(screen.getByText("物理学理论")).toBeTruthy();
    expect(screen.queryByText(/正文不应出现在合并态/)).toBeNull();
  });

  it("leaves a mixed tool group on the default chevron path", () => {
    render(
      <ToolLineGroup
        tools={[
          sources[0],
          step({
            id: "s1",
            tool_name: "web_search",
            arguments: { query: "天气" },
            result: "1 条",
            status: "success",
          }),
        ]}
        isStreaming={false}
      />,
    );
    // Default group summary (not the source-collection header).
    expect(screen.queryByText("Read page · 2 sources")).toBeNull();
    expect(screen.getByText(/Read page 1 · Search web 1/)).toBeTruthy();
  });
});

describe("ToolLineGroup · web_search 平铺", () => {
  function searchStep(
    id: string,
    query: string,
    resultCount: number,
  ): ToolStep {
    return step({
      id,
      tool_name: "web_search",
      arguments: { query },
      result: `${resultCount} results`,
      display: {
        query,
        results: Array.from({ length: resultCount }, (_, i) => ({
          title: `${query} hit ${i + 1}`,
          url: `https://example.com/${id}/${i}`,
          site: "example.com",
          snippet: "snippet",
        })),
      },
      status: "success",
    });
  }

  it("flattens ≥2 web_search into top-level rows without an outer group shell", () => {
    render(
      <ToolLineGroup
        tools={[
          searchStep("s1", "AgentCore 架构", 10),
          searchStep("s2", "Multi-Agent 协作", 10),
        ]}
        isStreaming={false}
      />,
    );
    // No concatenated outer summary (the old「Search web A · B」shell).
    expect(
      screen.queryByText(/Search web AgentCore 架构 · Multi-Agent 协作/),
    ).toBeNull();
    // Each search is a top-level row with its own query.
    expect(screen.getByText("AgentCore 架构")).toBeTruthy();
    expect(screen.getByText("Multi-Agent 协作")).toBeTruthy();
    // Result cards stay collapsed until the individual row is opened.
    expect(screen.queryByText("AgentCore 架构 hit 1")).toBeNull();
  });

  it("keeps a mixed search+other group on the default chevron path", () => {
    const { container } = render(
      <ToolLineGroup
        tools={[
          searchStep("s1", "天气", 3),
          step({
            id: "c1",
            tool_name: "code_execute",
            arguments: { code: "1+1" },
            result: "2",
            status: "success",
          }),
        ]}
        isStreaming={false}
      />,
    );
    expect(screen.getByText(/Search web 1 · Run code 1/)).toBeTruthy();
    fireEvent.click(screen.getByText(/Search web 1 · Run code 1/));
    expect(screen.getByText("天气")).toBeTruthy();
    expect(screen.queryByText(/3 results/)).toBeNull();
    expect(screen.queryByText("天气 hit 1")).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });
});

describe("ToolLineGroup · 混杂组不挂浏览器胶囊", () => {
  function browserStep(id: string, over?: Partial<ToolStep>): ToolStep {
    return step({
      id,
      tool_name: "browser_navigate",
      arguments: { url: "https://example.com" },
      result: "ok",
      status: "success",
      ...over,
    });
  }

  function unifiedBrowserStep(id: string, over?: Partial<ToolStep>): ToolStep {
    return step({
      id,
      tool_name: "browser",
      arguments: { action: "navigate", url: "https://example.com" },
      result: "ok",
      status: "success",
      ...over,
    });
  }

  it("does not hang 打开浏览器 on mixed unified-browser+other groups", () => {
    render(
      <ToolLineGroup
        tools={[
          unifiedBrowserStep("b1"),
          step({
            id: "c1",
            tool_name: "code_execute",
            arguments: { code: "1+1" },
            result: "2",
            status: "success",
          }),
        ]}
        isStreaming={false}
        conversationId="c1"
      />,
    );
    expect(screen.queryByText("打开浏览器")).toBeNull();
    expect(screen.queryByText("查看直播")).toBeNull();
  });

  it("does not hang 打开浏览器 on mixed browser+other groups", () => {
    render(
      <ToolLineGroup
        tools={[
          browserStep("b1"),
          step({
            id: "c1",
            tool_name: "code_execute",
            arguments: { code: "1+1" },
            result: "2",
            status: "success",
          }),
        ]}
        isStreaming={false}
        conversationId="c1"
      />,
    );
    expect(screen.queryByText("打开浏览器")).toBeNull();
    expect(screen.queryByText("查看直播")).toBeNull();
  });

  it("does not hang 查看直播 when a mixed-group step is running", () => {
    render(
      <ToolLineGroup
        tools={[
          browserStep("b1", { status: "running", result: null }),
          step({
            id: "c1",
            tool_name: "code_execute",
            arguments: { code: "1+1" },
            result: "2",
            status: "success",
          }),
        ]}
        isStreaming={true}
        conversationId="c1"
      />,
    );
    expect(screen.queryByText("查看直播")).toBeNull();
    expect(screen.queryByText("打开浏览器")).toBeNull();
  });
});

describe("ToolLineGroup · 折叠失败脸", () => {
  it("collapsed group shows 未通过, not a red failed badge", () => {
    render(
      <ToolLineGroup
        tools={[
          step({
            id: "f1",
            tool_name: "read",
            arguments: { file_path: "a.ts" },
            result: "ok",
            status: "success",
          }),
          step({
            id: "u1",
            tool_name: "run",
            arguments: { command: "npm test" },
            result: "测试未通过",
            status: "error",
            display: { stdout: "fail", stderr: "", exit_code: 1 },
          }),
        ]}
        isStreaming={false}
      />,
    );
    expect(screen.getByTestId("tool-group-fault").textContent).toBe("未通过");
    expect(screen.queryByText(/failed/i)).toBeNull();
    fireEvent.click(screen.getByText("Read file 1 · Run 1"));
    expect(screen.queryByTestId("tool-group-fault")).toBeNull();
    expect(screen.getByTestId("tool-fault-label").textContent).toBe("未通过");
  });
});

describe("ToolLine · handoff brief card", () => {
  const receipt = "已收尾。";

  it("collapsed face is 交接简报; protocol receipt stays hidden", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "handoff",
          arguments: { summary: "交叉验证完成，建议一周内表态" },
          result: receipt,
          status: "success",
        })}
      />,
    );
    expect(screen.getByRole("button", { name: "交接简报" })).toBeTruthy();
    expect(screen.queryByText("交叉验证完成，建议一周内表态")).toBeNull();
    expect(screen.queryByText("Handoff")).toBeNull();
    expect(screen.queryByText(receipt)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("summary-only still folds under 交接简报", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "handoff",
          arguments: { summary: "只写了结论" },
          result: receipt,
          status: "success",
        })}
      />,
    );
    expect(screen.getByRole("button", { name: "交接简报" })).toBeTruthy();
    expect(screen.queryByText("只写了结论")).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
    const face = screen.getByRole("button", { name: "交接简报" });
    expect(face.className).toContain("w-auto");
    expect(face.className).not.toContain("w-full");
    expect(container.querySelector(".lucide-chevron-right")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "交接简报" }));
    expect(screen.getByText("只写了结论")).toBeTruthy();
    expect(screen.queryByText("关键要点")).toBeNull();
    expect(screen.queryByText(receipt)).toBeNull();
  });

  it("keeps a long summary out of the collapsed face", () => {
    const long =
      "新增 packages/core/src/tools 工具系统（ToolName/Tool 契约 + 9 真实工具实现 + createTool 工厂），并把 engine.setTool 接入为真实实例切换。";
    render(
      <ToolLine
        step={step({
          tool_name: "handoff",
          arguments: {
            summary: long,
            key_points: ["共识：一周内需清晰立场"],
          },
          result: receipt,
          status: "success",
        })}
      />,
    );
    expect(screen.getByRole("button", { name: "交接简报" })).toBeTruthy();
    expect(screen.queryByText(long)).toBeNull();
    expect(screen.queryByText("关键要点")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "交接简报" }));
    expect(screen.getByText(long)).toBeTruthy();
  });

  it("expands body with summary then DebriefDetails", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "handoff",
          arguments: {
            summary: "交叉验证完成",
            key_points: ["共识：一周内需清晰立场"],
            assumptions: "争议事实以公开报道为准",
            next_steps: "若用户同意，建议开辩",
          },
          result: receipt,
          status: "success",
        })}
      />,
    );
    expect(screen.getByRole("button", { name: "交接简报" })).toBeTruthy();
    expect(screen.queryByText("交叉验证完成")).toBeNull();
    expect(screen.queryByText("Handoff")).toBeNull();
    expect(screen.queryByText("关键要点")).toBeNull();
    expect(container.querySelector(".lucide-chevron-right")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "交接简报" }));
    expect(screen.getByText("交叉验证完成")).toBeTruthy();
    expect(screen.getByText("关键要点")).toBeTruthy();
    expect(screen.getByText("共识：一周内需清晰立场")).toBeTruthy();
    expect(screen.getByText("关键假设")).toBeTruthy();
    expect(screen.getByText("建议下一步")).toBeTruthy();
    expect(screen.queryByText(receipt)).toBeNull();
  });

  it("keeps failed / running rows as ordinary tool lines", () => {
    const { rerender } = render(
      <ToolLine
        step={step({
          tool_name: "handoff",
          arguments: { summary: "半成品结论" },
          status: "running",
        })}
      />,
    );
    expect(screen.getByText("Handoff")).toBeTruthy();
    expect(screen.queryByText("半成品结论")).toBeNull();

    rerender(
      <ToolLine
        step={step({
          tool_name: "handoff",
          arguments: { summary: "半成品结论" },
          result: "空交付不得交接：本轮正文 0 字",
          status: "error",
          failure: {
            message: "空交付不得交接。",
            code: "HANDOFF_EMPTY",
          },
        })}
      />,
    );
    expect(screen.getByText("Handoff")).toBeTruthy();
    expect(screen.queryByText("空交付不得交接。")).toBeNull();
    expect(screen.queryByText("半成品结论")).toBeNull();
    expect(screen.queryByText("关键要点")).toBeNull();
    fireEvent.click(screen.getByText("Handoff"));
    expect(screen.getByText("空交付不得交接。")).toBeTruthy();
    expect(screen.getByText(/空交付不得交接：本轮正文 0 字/)).toBeTruthy();
    expect(screen.queryByTestId("tool-error-detail-toggle")).toBeNull();
  });
});

describe("ToolLine · ack 族成功无 peek", () => {
  it.each([
    {
      tool: "file_delete",
      label: "Delete file",
      args: { path: "gone.md" },
      ack: "已删除 gone.md",
      detail: "gone.md",
    },
    {
      tool: "file_move",
      label: "Move file",
      args: { source: "draft.md", destination: "out/final.md" },
      ack: "已移动 draft.md",
      detail: "draft.md → out/final.md",
    },
    {
      tool: "file_copy",
      label: "Copy file",
      args: { source: "a.md", destination: "b.md" },
      ack: "已复制 a.md",
      detail: "a.md → b.md",
    },
    {
      tool: "mkdir",
      label: "Make dir",
      args: { path: "out" },
      ack: "已创建目录 out",
      detail: "out",
    },
    {
      tool: "host",
      label: "Host shell",
      args: { action: "shell", command: "Get-Process" },
      ack: '{"exit_code":0}',
      detail: null,
    },
    {
      tool: "host_storage",
      label: "Host storage",
      args: {},
      ack: '{"disks":[{"name":"C:"}]}',
      detail: null,
    },
    {
      tool: "host_power",
      label: "Host power",
      args: {},
      ack: '{"battery":80}',
      detail: null,
    },
    {
      tool: "host_network_summary",
      label: "Network summary",
      args: {},
      ack: '{"ifaces":["eth0"]}',
      detail: null,
    },
    {
      tool: "host_apps",
      label: "Host apps",
      args: {},
      ack: '{"apps":["Notes"]}',
      detail: null,
    },
    {
      tool: "host_os_log_summary",
      label: "OS log summary",
      args: { source: "system" },
      ack: '{"events":[]}',
      detail: null,
    },
  ] as const)(
    "suppresses $tool success peek",
    ({ tool, label, args, ack, detail }) => {
      const { container } = render(
        <ToolLine
          step={step({
            tool_name: tool,
            arguments: { ...args },
            result: ack,
            status: "success",
          })}
        />,
      );
      expect(screen.getByText(label)).toBeTruthy();
      if (detail) expect(screen.getByText(detail)).toBeTruthy();
      expect(screen.queryByText(ack)).toBeNull();
      expect(collapsedSubline(container)).toBeNull();
    },
  );

  it.each([
    ["file_delete", "Delete file"],
    ["host", "Host status"],
    ["host_storage", "Host storage"],
  ] as const)("keeps collapsed %s error to one line", (tool, label) => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: tool,
          arguments: tool === "host" ? { action: "status" } : {},
          result: `${tool} boom: leaked internals`,
          status: "error",
          failure: {
            message: "操作失败，请稍后重试。",
            code: "TOOL_ERROR",
          },
        })}
      />,
    );
    expect(screen.getByText(label)).toBeTruthy();
    expect(screen.queryByText("操作失败，请稍后重试。")).toBeNull();
    expect(screen.queryByText(/leaked internals/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });
});

describe("ToolLine · browser action 标签", () => {
  it("认 browser + action，标签同构 host", () => {
    render(
      <ToolLine
        step={step({
          tool_name: "browser",
          arguments: { action: "navigate", url: "https://example.com" },
          result: "ok",
          display: {
            kind: "browser",
            action: "navigate",
            url: "https://example.com",
            detail: "打开 https://example.com",
          },
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Navigate")).toBeTruthy();
    expect(screen.getByText(/https:\/\/example.com/)).toBeTruthy();
    expect(screen.queryByText(/打开 https:\/\/example.com/)).toBeNull();
  });

  it("running browser step uses args.action, not slice of browser", () => {
    render(
      <ToolLine
        step={step({
          tool_name: "browser",
          arguments: { action: "click", ref: "e13" },
          status: "running",
          result: null,
        })}
      />,
    );
    expect(screen.getByText("Click")).toBeTruthy();
    expect(screen.queryByText(/^R$/)).toBeNull();
  });

  it("历史 browser_* 键仍走 TOOL_META 纯展示", () => {
    render(
      <ToolLine
        step={step({
          tool_name: "browser_click",
          arguments: { ref: "e13" },
          result: "ok",
          display: {
            kind: "browser",
            action: "click",
            url: "https://example.com",
            detail: "点击元素 e13",
          },
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Click")).toBeTruthy();
  });
});

describe("ToolLine · host action 标签", () => {
  it("认 host + action，标签同构 git subcommand", () => {
    render(
      <ToolLine
        step={step({
          tool_name: "host",
          arguments: {
            action: "install_package",
            manager: "winget",
            package_id: "Git.Git",
          },
          result: '{"ok":true}',
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Install package")).toBeTruthy();
    expect(screen.getByText("winget Git.Git")).toBeTruthy();
  });

  it("历史 host_* 键仍走 TOOL_META 纯展示", () => {
    render(
      <ToolLine
        step={step({
          tool_name: "host_shell",
          arguments: { command: "dir" },
          result: '{"ok":true}',
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Host shell")).toBeTruthy();
    expect(screen.queryByText("dir")).toBeNull();
  });

  it("shell 命令在展开里，不在折叠标题", () => {
    render(
      <ToolLine
        step={step({
          tool_name: "host",
          arguments: {
            action: "shell",
            command: "Get-CimInstance Win32_VideoController",
          },
          result: "ok",
          display: {
            stdout: "NVIDIA",
            stderr: "",
            exit_code: 0,
            language: "host",
          },
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Host shell")).toBeTruthy();
    expect(screen.queryByText(/Get-CimInstance/)).toBeNull();
    fireEvent.click(screen.getByText("Host shell"));
    expect(screen.getByText(/Get-CimInstance/)).toBeTruthy();
    expect(screen.getByText("NVIDIA")).toBeTruthy();
  });
});

describe("toolDetail · title chip", () => {
  it("prefers path / name over long prose bodies", () => {
    expect(toolDetail({ path: "a/b.md", draft: "## 长草稿\n更多" })).toBe(
      "a/b.md",
    );
    expect(toolDetail({ name: "部署流程" })).toBe("部署流程");
  });

  it("绝不把内部标识摆进标题（用户对不上协作图上的角色名）", () => {
    expect(
      toolDetail({ run_id: "r-a3f2e1c8-9b21", answer: "很长的裁决正文……" }),
    ).toBe("");
    expect(toolDetail({ conversation_id: "c-8f31ab02" })).toBe("");
    expect(toolDetail({ interjection_id: "i-77120c9a" })).toBe("");
  });

  it("read_conversation 不把查找词摆进标题（对话名走 peek）", () => {
    expect(
      toolDetail(
        { conversation_id: "c-8f31ab02", query: "适配" },
        "read_conversation",
      ),
    ).toBe("");
    expect(toolDetail({ query: "适配" }, "web_search")).toBe("适配");
    expect(toolDetail({ query: "适配" }, "search_conversations")).toBe("适配");
  });

  it("does not leak update_synthesis draft into the title", () => {
    expect(
      toolDetail({
        draft: "## 进展简报\n| 队员 | 状态 |\n| --- | --- |",
      }),
    ).toBe("");
  });

  it("still chips a short one-line code snippet", () => {
    expect(toolDetail({ code: "print(1)" })).toBe("print(1)");
    expect(toolDetail({ code: "line1\nline2\nline3\nline4\nline5" })).toBe("");
  });

  it("prefers language / check over short code for execute tools", () => {
    expect(
      toolDetail({ code: "print(1)", language: "python" }, "code_execute"),
    ).toBe("python");
    expect(
      toolDetail({ code: "vitest run", check: "typecheck" }, "test_run"),
    ).toBe("typecheck");
    expect(
      toolDetail({ check: "command", command: "pnpm test" }, "test_run"),
    ).toBe("");
    expect(toolDetail({ command: "curl.exe" }, "run")).toBe("");
    expect(toolDetail({ command: "pnpm test" }, "terminal")).toBe("");
    expect(toolDetail({ command: "dir" }, "host_shell")).toBe("");
  });

  it("does not chip handoff summary into toolDetail (ToolLine inlines peek instead)", () => {
    expect(toolDetail({ summary: "交叉验证完成，建议一周内表态" })).toBe("");
  });

  it("browser 按 action 出细节", () => {
    expect(
      toolDetail({ action: "navigate", url: "https://example.com" }, "browser"),
    ).toBe("https://example.com");
    expect(toolDetail({ action: "click", ref: "e13" }, "browser")).toBe("");
    expect(
      toolDetail({ action: "type", ref: "e2", text: "hello" }, "browser"),
    ).toBe("hello");
    expect(toolDetail({ action: "scroll", dy: 400 }, "browser")).toBe("");
  });

  it("host 按 action 出细节", () => {
    expect(
      toolDetail({ action: "shell", command: "Get-Process" }, "host"),
    ).toBe("");
    expect(
      toolDetail(
        {
          action: "install_package",
          manager: "brew",
          package_id: "cask",
          cask: true,
        },
        "host",
      ),
    ).toBe("brew cask (cask)");
  });

  it("chips file_move / file_copy source → destination, not a lone verb", () => {
    expect(
      toolDetail({ source: "draft.md", destination: "out/final.md" }),
    ).toBe("draft.md → out/final.md");
    expect(toolDetail({ source: "a.md", destination: "b.md" })).toBe(
      "a.md → b.md",
    );
    expect(toolDetail({ source: "only-src.md" })).toBe("");
    expect(toolDetail({ destination: "only-dest.md" })).toBe("");
  });

  it("chips directory for file_list; skips '.' and folder_id UUID", () => {
    expect(toolDetail({ directory: "src/app" }, "file_list")).toBe("src/app");
    expect(toolDetail({ directory: "." }, "file_list")).toBe("");
    expect(
      toolDetail(
        { folder_id: "550e8400-e29b-41d4-a716-446655440000" },
        "delete_folder",
      ),
    ).toBe("");
    expect(
      toolDetail({ file_path: "550e8400-e29b-41d4-a716-446655440000" }, "read"),
    ).toBe("");
  });

  it("chips file_path for read / write / edit; keeps path for file_delete", () => {
    expect(toolDetail({ file_path: "src/a.ts" }, "read")).toBe("src/a.ts");
    expect(toolDetail({ path: "src/a.ts" }, "read")).toBe("");
    expect(toolDetail({ file_path: "src/a.ts", content: "x" }, "write")).toBe(
      "src/a.ts",
    );
    expect(toolDetail({ path: "src/a.ts", content: "x" }, "write")).toBe("");
    expect(toolDetail({ file_path: "src/a.ts" }, "edit")).toBe("src/a.ts");
    expect(toolDetail({ path: "gone.txt" }, "file_delete")).toBe("gone.txt");
    expect(toolDetail({ file_path: "gone.txt" }, "file_delete")).toBe("");
  });

  it("git title chip is subcommand, not ApprovalPrompt headline", () => {
    expect(
      toolDetail(
        {
          subcommand: "commit",
          message: "feat: fold process tools into one line",
        },
        "git",
      ),
    ).toBe("commit");
    expect(toolDetail({ subcommand: "push", remote: "origin" }, "git")).toBe(
      "push",
    );
    expect(toolDetail({ subcommand: "status" }, "git")).toBe("status");
  });
});

describe("toolGroupSummary · web_fetch", () => {
  it("uses a count title instead of URL basenames", () => {
    const tools = [
      step({
        id: "a",
        tool_name: "web_fetch",
        arguments: {
          url: "https://zhuanlan.zhihu.com/p/1050596771_121124370",
        },
      }),
      step({
        id: "b",
        tool_name: "web_fetch",
        arguments: { url: "https://baike.baidu.com/item/相对论" },
      }),
    ];
    expect(toolGroupSummary(tools)).toBe("Read page · 2 sources");
    expect(toolGroupSummary(tools)).not.toMatch(/1050596771/);
  });

  it("keeps basename titles for other same-kind groups", () => {
    const tools = [
      step({
        id: "a",
        tool_name: "read",
        arguments: { file_path: "src/foo.ts" },
      }),
      step({
        id: "b",
        tool_name: "read",
        arguments: { file_path: "src/bar.ts" },
      }),
    ];
    expect(toolGroupSummary(tools)).toBe("Read file foo.ts · bar.ts");
  });

  it("host shell 组头计数，不拼命令", () => {
    const tools = [
      step({
        id: "a",
        tool_name: "host",
        arguments: {
          action: "shell",
          command: "Get-CimInstance Win32_VideoController",
        },
      }),
      step({
        id: "b",
        tool_name: "host",
        arguments: {
          action: "shell",
          command: '"=== nvidia-smi ==="; nvidia-smi',
        },
      }),
    ];
    expect(toolGroupSummary(tools)).toBe("Host shell 2");
    expect(toolGroupSummary(tools)).not.toMatch(/nvidia-smi|Get-CimInstance/);
  });
});

describe("ComposingToolLine · 参数组装心跳", () => {
  it("shows tool label only for non-write tools — no 正在组装 / Composing / 字", () => {
    const { container } = renderWithTooltip(
      <ComposingToolLine tool={{ toolName: "web_search", chars: 1280 }} />,
    );
    expect(screen.getByText("Search web")).toBeTruthy();
    expect(container.querySelector("[data-live-flow]")).not.toBeNull();
    expect(screen.queryByText(/正在组装/)).toBeNull();
    expect(screen.queryByText(/Composing/i)).toBeNull();
    expect(screen.queryByText(/字/)).toBeNull();
    expect(screen.queryByText(/chars/i)).toBeNull();
  });

  it("write family shows label + char count, no verb prefix", () => {
    renderWithTooltip(
      <ComposingToolLine tool={{ toolName: "write", chars: 2100 }} />,
    );
    expect(screen.getByText(/Write file/)).toBeTruthy();
    expect(screen.getByText(/2\.1k 字/)).toBeTruthy();
    expect(screen.queryByText(/正在组装/)).toBeNull();
  });

  it("delegate / debate composing also shows char count, no verb prefix", () => {
    const { unmount } = renderWithTooltip(
      <ComposingToolLine tool={{ toolName: "delegate", chars: 2100 }} />,
    );
    expect(screen.getByText(/Delegate/)).toBeTruthy();
    expect(screen.getByText(/2\.1k 字/)).toBeTruthy();
    expect(screen.queryByText(/正在组装/)).toBeNull();
    unmount();
    renderWithTooltip(
      <ComposingToolLine tool={{ toolName: "debate", chars: 4200 }} />,
    );
    expect(screen.getByText(/Debate/)).toBeTruthy();
    expect(screen.getByText(/4\.2k 字/)).toBeTruthy();
  });

  it("omits char count when zero", () => {
    renderWithTooltip(
      <ComposingToolLine tool={{ toolName: "debate", chars: 0 }} />,
    );
    expect(screen.getByText("Debate")).toBeTruthy();
    expect(screen.queryByText(/正在组装/)).toBeNull();
    expect(screen.queryByText(/字/)).toBeNull();
  });

  it("does not paint a block composing caret", () => {
    const { unmount } = renderWithTooltip(
      <ComposingToolLine tool={{ toolName: "write", chars: 2100 }} />,
    );
    expect(screen.queryByText("▋")).toBeNull();
    unmount();
    renderWithTooltip(
      <ComposingToolLine tool={{ toolName: "web_search", chars: 1280 }} />,
    );
    expect(screen.queryByText("▋")).toBeNull();
  });
});

describe("ToolLine · tool_use_end.failure product face", () => {
  it("keeps the collapsed row to one line (no generic failure copy)", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "web_search",
          arguments: { query: "AgentCore" },
          result:
            "搜索失败：ConnectError: [Errno 111] Connection refused to searxng.internal:8080",
          status: "error",
          failure: {
            message: GENERIC_TOOL_FAILURE_MESSAGE,
            code: "TOOL_ERROR",
          },
        })}
      />,
    );
    expect(screen.getByText("Search web")).toBeTruthy();
    expect(screen.queryByText(GENERIC_TOOL_FAILURE_MESSAGE)).toBeNull();
    expect(screen.queryByText(/searxng\.internal/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("shows a specific product sentence only after expand, with technical result", () => {
    renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "web_search",
          arguments: { query: "AgentCore" },
          result:
            "搜索失败：ConnectError: [Errno 111] Connection refused to searxng.internal:8080",
          status: "error",
          failure: {
            message: "搜索服务暂时不可用，请稍后重试。",
            code: "HOST_UNAVAILABLE",
          },
        })}
      />,
    );
    expect(screen.queryByText("搜索服务暂时不可用，请稍后重试。")).toBeNull();
    expect(screen.queryByText(/searxng\.internal/)).toBeNull();
    fireEvent.click(screen.getByText("Search web"));
    expect(screen.getByText("搜索服务暂时不可用，请稍后重试。")).toBeTruthy();
    expect(screen.getByText(/searxng\.internal:8080/)).toBeTruthy();
    expect(screen.queryByTestId("tool-error-detail-toggle")).toBeNull();
  });

  it("does not peek technical result on a collapsed error row", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "code_execute",
          arguments: {},
          result: "ExecEnvProbeFailed: 127.0.0.1:5432",
          status: "error",
        })}
      />,
    );
    expect(screen.queryByText(/ExecEnvProbeFailed/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
    fireEvent.click(screen.getByText("Run code"));
    expect(screen.getByText(/ExecEnvProbeFailed: 127.0.0.1:5432/)).toBeTruthy();
    expect(screen.queryByTestId("tool-error-detail-toggle")).toBeNull();
  });

  it("peek-suppressed tools stay one line until expand; lookup miss skips the extra sentence", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "read",
          arguments: { file_path: "missing.md" },
          result: "FileNotFoundError: missing.md",
          status: "error",
          failure: {
            message: "读取文件失败。",
            code: "FILE_NOT_FOUND",
          },
        })}
      />,
    );
    expect(screen.queryByText("读取文件失败。")).toBeNull();
    expect(screen.queryByText(/FileNotFoundError/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
    fireEvent.click(screen.getByText("Read file"));
    expect(screen.queryByText("读取文件失败。")).toBeNull();
    expect(screen.getByText(/FileNotFoundError/)).toBeTruthy();
    expect(screen.queryByTestId("tool-error-detail-toggle")).toBeNull();
  });
});

describe("ToolLine · git 执行相位", () => {
  // git can sit ~2min behind the repo queue, a credential lookup and a remote round
  // trip. Those waits name the leg. Ordinary local execution stays unlabeled: the
  // title sheen already says the row is in flight.
  it.each([
    ["git_queued", "Waiting for repo"],
    ["git_credentials", "Checking credentials"],
    ["git_remote", "Contacting remote"],
  ] as const)("shows %s as「%s」while the call is in flight", (phase, text) => {
    render(
      <ToolLine
        step={step({
          tool_name: "git",
          arguments: { subcommand: "push" },
          result: null,
          status: "running",
          phase,
        })}
      />,
    );
    expect(screen.getByText(text)).toBeTruthy();
  });

  it("leaves ordinary local execution and an unknown phase unlabeled", () => {
    const { unmount } = render(
      <ToolLine
        step={step({
          tool_name: "run",
          arguments: { command: "curl.exe" },
          result: null,
          status: "running",
          phase: "executing",
        })}
      />,
    );
    expect(screen.queryByText("Running")).toBeNull();
    expect(screen.queryByText("Working")).toBeNull();
    unmount();
    render(
      <ToolLine
        step={step({
          tool_name: "git",
          arguments: { subcommand: "push" },
          result: null,
          status: "running",
          phase: "git_future_leg" as never,
        })}
      />,
    );
    expect(screen.queryByText("Running")).toBeNull();
    expect(screen.queryByText("Working")).toBeNull();
    expect(screen.getByText("Git")).toBeTruthy();
  });
});

describe("ToolLine · test_run incomplete", () => {
  it("shows warning affordance instead of fault-red ✗", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "test_run",
          arguments: { check: "typecheck" },
          result: "验证未取得完整结果（已中止）",
          status: "error",
          display: {
            check: "typecheck",
            exit_code: -1,
            stdout: "",
            stderr: "Timeout: no timeout_kind",
            budget_exceeded: true,
          },
        })}
      />,
    );
    expect(container.querySelector(".text-destructive")).toBeNull();
    expect(container.querySelector(".text-warning")).toBeTruthy();
    expect(container.textContent).toContain("验证未完成");
    expect(container.textContent).not.toContain("预算耗尽");
    expect(collapsedSubline(container)).toBeNull();
  });

  it("idle hang face is warning, not fault red", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "test_run",
          arguments: { check: "typecheck" },
          result: "执行长时间无输出，已按挂起中止",
          status: "error",
          display: {
            check: "typecheck",
            exit_code: -1,
            stdout: "",
            stderr: "idle timeout",
            budget_exceeded: true,
            timeout_kind: "idle",
          },
        })}
      />,
    );
    expect(container.querySelector(".text-destructive")).toBeNull();
    expect(container.querySelector(".text-warning")).toBeTruthy();
    expect(container.textContent).toContain("执行无响应（无输出已中止）");
    expect(container.textContent).not.toContain("预算耗尽");
    expect(collapsedSubline(container)).toBeNull();
  });

  it("disaster wall face is warning, not fault red", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "test_run",
          arguments: { check: "typecheck" },
          result: "已跑满灾难顶，强制中止",
          status: "error",
          display: {
            check: "typecheck",
            exit_code: -1,
            stdout: "",
            stderr: "forced stop",
            budget_exceeded: true,
            timeout_kind: "disaster",
          },
        })}
      />,
    );
    expect(container.querySelector(".text-destructive")).toBeNull();
    expect(container.querySelector(".text-warning")).toBeTruthy();
    expect(container.textContent).toContain("执行已强制中止");
    expect(container.textContent).not.toContain("预算耗尽");
    expect(collapsedSubline(container)).toBeNull();
  });
});

describe("ToolLine · code_execute / test_run / terminal 一行契约", () => {
  it("test_run success shows check in title, not stdout banner", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "test_run",
          arguments: { check: "typecheck" },
          result: "stdout:\n== 总量 ==\n0 errors",
          display: {
            check: "typecheck",
            stdout: "== 总量 ==\n0 errors",
            stderr: "",
            exit_code: 0,
          },
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("typecheck")).toBeTruthy();
    expect(screen.queryByText(/== 总量 ==/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("code_execute failure shows 未通过, not a fault X or exit number", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "code_execute",
          arguments: { code: "raise SystemExit(1)", language: "python" },
          result: "boom",
          status: "error",
          display: { stdout: "", stderr: "boom", exit_code: 1 },
        })}
      />,
    );
    expect(screen.getByText("python")).toBeTruthy();
    expect(screen.getByTestId("tool-fault-label").textContent).toBe("未通过");
    expect(screen.queryByText(/退出码 1/)).toBeNull();
    expect(container.querySelector(".text-destructive")).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
    fireEvent.click(screen.getByText("Run code"));
    expect(screen.getByText("boom")).toBeTruthy();
    expect(screen.getByTestId("tool-fault-label").textContent).toBe("未通过");
    expect(screen.queryByText(/退出码 1/)).toBeNull();
  });

  it("run error with exit 0 shows 未通过, not 0 or 1", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "run",
          arguments: { command: "npm test 2>&1 | tail -60" },
          result: "测试未通过（失败 1）",
          status: "error",
          display: { stdout: "1 failed", stderr: "", exit_code: 0 },
        })}
      />,
    );
    expect(screen.getByTestId("tool-fault-label").textContent).toBe("未通过");
    expect(screen.queryByTestId("tool-exit-code")).toBeNull();
    expect(container.querySelector(".text-destructive")).toBeNull();
    expect(screen.queryByText(/退出码/)).toBeNull();
  });

  it("terminal success keeps the action label; command and stdout stay collapsed away", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "terminal",
          arguments: { command: "pnpm test" },
          result: "first line of output\nmore",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Run terminal")).toBeTruthy();
    expect(screen.queryByText("pnpm test")).toBeNull();
    expect(screen.queryByText(/first line of output/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
    fireEvent.click(screen.getByText("Run terminal"));
    expect(screen.getByText("pnpm test")).toBeTruthy();
    expect(screen.getByText(/first line of output/)).toBeTruthy();
  });
});

describe("ToolLine · channel redirect", () => {
  it("titles the row 改用搜索 without a fault X or model steer", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "code_execute",
          arguments: { code: "open('index.html').read()" },
          result:
            "禁止用 code_execute 打开源码再正则扫描（检测到：re.findall(）。",
          status: "redirect",
          failure: {
            message:
              "这一步想用脚本打开源码再搜索，没有执行。我会改用搜索工具定位后再读文件。",
            code: "source_grep_redirect",
          },
        })}
      />,
    );
    expect(screen.getByText("改用搜索")).toBeTruthy();
    expect(screen.queryByText("Run code")).toBeNull();
    expect(container.querySelector(".text-destructive")).toBeNull();
    expect(screen.queryByText(/禁止用/)).toBeNull();
    fireEvent.click(screen.getByText("改用搜索"));
    expect(screen.queryByText(/我会改用搜索工具定位后再读文件/)).toBeNull();
  });

  it("normalizes a legacy error + redirect code the same way", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "code_execute",
          arguments: { code: "open('index.html').read()" },
          result:
            "禁止用 code_execute 打开源码再正则扫描（检测到：re.findall(）。",
          status: "error",
          failure: {
            message:
              "这一步想用脚本打开源码再搜索，没有执行。我会改用搜索工具定位后再读文件。",
            code: "source_grep_redirect",
          },
        })}
      />,
    );
    expect(screen.getByText("改用搜索")).toBeTruthy();
    expect(container.querySelector(".text-destructive")).toBeNull();
    expect(screen.queryByText(/禁止用/)).toBeNull();
  });
});
