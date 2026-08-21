// @vitest-environment jsdom
/**
 * Render test for ToolLine 过程工具默认折叠: every process tool (web_search / code_execute /
 * file_write / str_replace / …) stays collapsed on the running→done edge — aligned with
 * Cursor/Claude「过程收敛、答案突出」. Folded rows keep inlineMeta / inlineBody /
 * peek; expand is a click away. Failures also stay collapsed (red ✗ + red peek).
 * The block comment detaches the
 * @vitest-environment directive from the import block so organizeImports keeps it file-leading.
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import type { ProcessStep } from "@/types/events";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const showBrowser = vi.fn();
vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: Object.assign(
    (selector: (s: { showBrowser: typeof showBrowser }) => unknown) =>
      selector({ showBrowser }),
    { getState: () => ({ showBrowser }) },
  ),
}));

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}));

import { ComposingToolLine, ToolLine, ToolLineGroup } from "../ToolLine";
import { toolDetail, toolGroupSummary } from "../message-bubble/constants";

afterEach(cleanup);

beforeEach(() => {
  showBrowser.mockReset();
});

function renderWithTooltip(ui: ReactElement) {
  return render(<TooltipProvider>{ui}</TooltipProvider>);
}

/** Collapsed result subline (`text-xs` under the title). Null when the row is one line. */
function collapsedSubline(container: HTMLElement): HTMLElement | null {
  return container.querySelector("span.block.truncate.text-xs");
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
    tool_name: "read_url",
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
    const { rerender } = render(
      <ToolLine
        step={step({
          tool_name: "code_execute",
          arguments: { code: "print('hi')", language: "python" },
          status: "running",
        })}
      />,
    );
    // Running: nothing to expand yet — the terminal (退出码 badge) is absent.
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
    // Done: stays collapsed — 退出码 badge is expanded-only; peek shows stdout.
    expect(screen.queryByText(/退出码 0/)).toBeNull();
    expect(screen.getByText(/hello world/)).toBeTruthy();

    fireEvent.click(screen.getByText("Run code"));
    expect(screen.getByText(/退出码 0/)).toBeTruthy();
  });

  it("keeps web_search results collapsed on completion", () => {
    const { rerender } = render(
      <ToolLine
        step={step({
          tool_name: "web_search",
          arguments: { query: "深圳天气" },
          status: "running",
        })}
      />,
    );
    // The hit title is expanded-only (while running only the query detail shows).
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
    // Collapsed: hit title hidden; click reveals the result card.
    expect(screen.queryByText("深圳天气预报")).toBeNull();
    fireEvent.click(screen.getByText("Search web"));
    expect(screen.getByText("深圳天气预报")).toBeTruthy();
  });

  it("inlines web_search result count into the title row when collapsed", () => {
    const { rerender } = render(
      <ToolLine
        step={step({
          tool_name: "web_search",
          arguments: { query: "深圳天气" },
          status: "running",
        })}
      />,
    );
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
    // Already collapsed by default — 计数并入标题行（对齐 read_url 组的「· N sources」），
    // 不再另起一行 peek；结果卡标题隐藏。
    expect(screen.getByText(/1 result/)).toBeTruthy();
    expect(screen.queryByText("深圳天气预报")).toBeNull();
  });

  it("keeps str_replace diff collapsed on the running→done edge", () => {
    const { rerender, container } = render(
      <ToolLine
        step={step({
          tool_name: "str_replace",
          arguments: {
            path: "src/foo.ts",
            old_string: "const x = 1",
            new_string: "const x = 2",
          },
          status: "running",
        })}
      />,
    );
    // Collapsed peek only — the diff's +/- counts are expanded-only.
    expect(screen.queryByText("+1")).toBeNull();

    rerender(
      <ToolLine
        step={step({
          tool_name: "str_replace",
          arguments: {
            path: "src/foo.ts",
            old_string: "const x = 1",
            new_string: "const x = 2",
          },
          result: "已编辑 src/foo.ts",
          status: "success",
        })}
      />,
    );
    expect(screen.queryByText("+1")).toBeNull();
    expect(screen.queryByText("-1")).toBeNull();
    expect(screen.queryByText(/已编辑/)).toBeNull();
    expect(screen.getByText("src/foo.ts")).toBeTruthy();
    expect(collapsedSubline(container)).toBeNull();

    fireEvent.click(screen.getByText("Edit file"));
    expect(screen.getByText("+1")).toBeTruthy();
    expect(screen.getByText("-1")).toBeTruthy();
  });

  it("keeps file_write content card collapsed on the running→done edge", () => {
    const { rerender, container } = render(
      <ToolLine
        step={step({
          tool_name: "file_write",
          arguments: { path: "src/new.ts", content: "export const x = 1" },
          status: "running",
        })}
      />,
    );
    // Line-count footer is expanded-only; collapsed row is title + path only.
    expect(screen.queryByText(/1 行 ·/)).toBeNull();

    rerender(
      <ToolLine
        step={step({
          tool_name: "file_write",
          arguments: { path: "src/new.ts", content: "export const x = 1" },
          result: "已写入 src/new.ts",
          status: "success",
        })}
      />,
    );
    expect(screen.queryByText(/1 行 ·/)).toBeNull();
    expect(screen.queryByText(/已写入/)).toBeNull();
    expect(screen.getByText("src/new.ts")).toBeTruthy();
    expect(collapsedSubline(container)).toBeNull();

    fireEvent.click(screen.getByText("Write file"));
    expect(screen.getByText(/1 行 ·/)).toBeTruthy();
  });

  it("inlines write diagnostics into the title and stays one line", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "file_write",
          arguments: { path: "src/new.ts", content: "export const x = 1" },
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
    expect(screen.queryByText(/已写入/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("suppresses file_append ack peek — title path is enough", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "file_append",
          arguments: { path: "notes.md", content: "tail" },
          result: "已追加 notes.md",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Append file")).toBeTruthy();
    expect(screen.getByText("notes.md")).toBeTruthy();
    expect(screen.queryByText(/已追加/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("inlines grep match count into the title row when collapsed", () => {
    const { container } = render(
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
    expect(screen.getByText(/1 处匹配 · 1 个文件/)).toBeTruthy();
    expect(screen.getByText(/1 处匹配 · 1 个文件/).className).toMatch(
      /max-w-\[40%\]/,
    );
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
    render(
      <ToolLine
        step={step({
          tool_name: "consult",
          arguments: { name: "部署流程" },
          result: "用 pnpm dev 起前端",
          display: { name: "部署流程", kind: "memory" },
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Consult")).toBeTruthy();
    expect(screen.queryByText(/用 pnpm dev 起前端/)).toBeNull();
    expect(screen.getAllByText("部署流程")).toHaveLength(1);
  });

  it("read_conversation 折叠态亮对话标题（不摆 conversation_id、不泄正文）", () => {
    render(
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
    expect(screen.getByText("上周方案")).toBeTruthy();
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

  it("suppresses file_read / file_list result-first-line peeks", () => {
    const { rerender } = render(
      <ToolLine
        step={step({
          tool_name: "file_read",
          arguments: { path: "lv_jasmine_report/lv_jasmine_synthesis.md" },
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

  it("chips run_id for resolve_escalation without dumping answer or ack peek", () => {
    const ack = "已将裁决回传给 worker run_legal_1，队员将据此继续。";
    render(
      <ToolLine
        step={step({
          tool_name: "resolve_escalation",
          arguments: {
            run_id: "run_legal_1",
            answer: "请按公司法 §20 继续，详细论述如下……\n第二段。",
          },
          result: ack,
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Resolve escalate")).toBeTruthy();
    expect(screen.getByText("run_legal_1")).toBeTruthy();
    expect(screen.queryByText(/请按公司法/)).toBeNull();
    expect(screen.queryByText(ack)).toBeNull();
  });

  it("leaves read_url collapsed on completion (same default as every other tool)", () => {
    const { rerender } = render(
      <ToolLine
        step={step({
          tool_name: "read_url",
          arguments: { url: "https://weather.example.com/sz" },
          status: "running",
        })}
      />,
    );
    rerender(
      <ToolLine
        step={step({
          tool_name: "read_url",
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
    // Collapsed peek shows「标题 · 域名」; body stays hidden until the user expands.
    expect(screen.getByText("深圳天气 · weather.example.com")).toBeTruthy();
    expect(screen.queryByText(/正文预览不应自动展开/)).toBeNull();
  });
});

describe("ToolLine · browser 单步折叠一行", () => {
  it("inlines click detail into the title and drops the peek line", () => {
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
            detail: "点击元素 e13",
          },
          status: "success",
        })}
      />,
    );
    expect(screen.getAllByText("Click")).toHaveLength(1);
    expect(screen.getByText(/点击元素 e13/)).toBeTruthy();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("does not also chip navigate url when display.detail is present", () => {
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
    expect(screen.getByText(/打开 https:\/\/example.com/)).toBeTruthy();
    expect(collapsedSubline(container)).toBeNull();
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
    expect(screen.queryByText(/点击元素/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("surfaces failure.message on the collapsed row", () => {
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
    expect(screen.getByText("未找到元素 e13。")).toBeTruthy();
    expect(screen.queryByText(/ElementNotFound/)).toBeNull();
    expect(collapsedSubline(container)).toBeTruthy();
  });
});

describe("ToolLineGroup · read_url 来源集合", () => {
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

  it("merges ≥2 read_url into a count-title header without collapsed pills", () => {
    renderWithTooltip(<ToolLineGroup tools={sources} isStreaming={false} />);
    expect(screen.getByText("Read page · 2 sources")).toBeTruthy();
    // 折叠态收敛为纯标题行（对齐工具组 / 思考过程）——来源 pills 移到展开态，不再平铺。
    expect(screen.queryByText("zhuanlan.zhihu.com")).toBeNull();
    expect(screen.queryByText("baike.baidu.com")).toBeNull();
    // Merged view does not inline page bodies.
    expect(screen.queryByText(/正文不应出现在合并态/)).toBeNull();
  });

  it("expands to a SourceCards-style list without body content", () => {
    renderWithTooltip(<ToolLineGroup tools={sources} isStreaming={false} />);
    fireEvent.click(screen.getByText("Read page · 2 sources"));
    expect(screen.getByText("相对论入门")).toBeTruthy();
    expect(screen.getByText("相对论")).toBeTruthy(); // cleanSourceTitle strips _百度百科
    expect(screen.getByText("时空弯曲简介")).toBeTruthy();
    // 来源域名在展开态才出现（折叠态已无 pills）。
    expect(screen.getByText("zhuanlan.zhihu.com")).toBeTruthy();
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
    render(
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
  });
});

describe("ToolLineGroup · 混杂组浏览器 CTA", () => {
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

  it("shows a single group-header CTA for mixed unified-browser+other groups", () => {
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
    const ctas = screen.getAllByText("打开浏览器");
    expect(ctas).toHaveLength(1);
    fireEvent.click(ctas[0]);
    expect(showBrowser).toHaveBeenCalledTimes(1);
  });

  it("shows a single group-header CTA for mixed browser+other groups", () => {
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
    const ctas = screen.getAllByText("打开浏览器");
    expect(ctas).toHaveLength(1);
    fireEvent.click(ctas[0]);
    expect(showBrowser).toHaveBeenCalledTimes(1);
  });

  it("labels the CTA 查看直播 when any step is running", () => {
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
    expect(screen.getByText("查看直播")).toBeTruthy();
    expect(screen.queryByText("打开浏览器")).toBeNull();
  });

  it("does not show a browser CTA for pure non-browser groups", () => {
    render(
      <ToolLineGroup
        tools={[
          step({
            id: "c1",
            tool_name: "code_execute",
            arguments: { code: "1+1" },
            result: "2",
            status: "success",
          }),
          step({
            id: "f1",
            tool_name: "file_read",
            arguments: { path: "a.ts" },
            result: "ok",
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
});

describe("ToolLine · handoff brief card", () => {
  const receipt = "已收尾并提交交接简报。";

  it("peeks arguments.summary and never the protocol receipt", () => {
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
    expect(screen.getByText("Handoff")).toBeTruthy();
    expect(screen.getByText("交叉验证完成，建议一周内表态")).toBeTruthy();
    expect(screen.queryByText(receipt)).toBeNull();
    expect(screen.queryByText("交接简报")).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
  });

  it("summary-only has no chevron and does not expand", () => {
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
    expect(screen.getByText("只写了结论")).toBeTruthy();
    expect(collapsedSubline(container)).toBeNull();
    expect(container.querySelector(".lucide-chevron-right")).toBeNull();
    expect(container.querySelector(".lucide-chevron-down")).toBeNull();
    fireEvent.click(screen.getByText("Handoff"));
    expect(screen.queryByText("关键要点")).toBeNull();
    expect(screen.queryByText(receipt)).toBeNull();
  });

  it("expands details with DebriefDetails layout, no 交接简报 heading", () => {
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
    expect(screen.getByText("交叉验证完成")).toBeTruthy();
    expect(screen.queryByText("关键要点")).toBeNull();
    expect(container.querySelector(".lucide-chevron-right")).toBeTruthy();
    fireEvent.click(screen.getByText("Handoff"));
    expect(screen.getByText("关键要点")).toBeTruthy();
    expect(screen.getByText("共识：一周内需清晰立场")).toBeTruthy();
    expect(screen.getByText("关键假设")).toBeTruthy();
    expect(screen.getByText("建议下一步")).toBeTruthy();
    expect(screen.queryByText("交接简报")).toBeNull();
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
    expect(screen.getByText("空交付不得交接。")).toBeTruthy();
    expect(screen.queryByText("半成品结论")).toBeNull();
    expect(screen.queryByText("关键要点")).toBeNull();
  });
});

describe("ToolLine · wait 一行收口", () => {
  it("successful wait is one line: no peek, no chevron", () => {
    const { container } = render(
      <ToolLine
        step={step({
          tool_name: "wait",
          arguments: {},
          result: "已等待队员回合结束。",
          status: "success",
        })}
      />,
    );
    expect(screen.getByText("Wait")).toBeTruthy();
    expect(screen.queryByText(/已等待/)).toBeNull();
    expect(collapsedSubline(container)).toBeNull();
    expect(container.querySelector(".lucide-chevron-right")).toBeNull();
    expect(container.querySelector(".lucide-chevron-down")).toBeNull();
    fireEvent.click(screen.getByText("Wait"));
    expect(screen.queryByText(/已等待/)).toBeNull();
  });

  it("failed wait still shows the product failure face", () => {
    render(
      <ToolLine
        step={step({
          tool_name: "wait",
          arguments: {},
          result: "WaitError: internal timeout",
          status: "error",
          failure: {
            message: "等待队员超时。",
            code: "WAIT_TIMEOUT",
          },
        })}
      />,
    );
    expect(screen.getByText("等待队员超时。")).toBeTruthy();
    expect(screen.queryByText(/WaitError/)).toBeNull();
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
      detail: "Get-Process",
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
    {
      tool: "post_note",
      label: "Post note",
      args: {},
      ack: "已发布便签。",
      detail: null,
    },
    {
      tool: "amend_note",
      label: "Amend note",
      args: {},
      ack: "已修订便签。",
      detail: null,
    },
    {
      tool: "desktop_notify",
      label: "Notify",
      args: {},
      ack: "已发送桌面通知。",
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
    ["post_note", "Post note"],
    ["desktop_notify", "Notify"],
  ] as const)("surfaces failure.message for %s", (tool, label) => {
    render(
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
    expect(screen.getByText("操作失败，请稍后重试。")).toBeTruthy();
    expect(screen.queryByText(/leaked internals/)).toBeNull();
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
    expect(screen.getByText(/打开 https:\/\/example.com/)).toBeTruthy();
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

  it("does not chip handoff summary into toolDetail (ToolLine inlines peek instead)", () => {
    expect(toolDetail({ summary: "交叉验证完成，建议一周内表态" })).toBe("");
  });

  it("browser 按 action 出细节", () => {
    expect(
      toolDetail({ action: "navigate", url: "https://example.com" }, "browser"),
    ).toBe("https://example.com");
    expect(toolDetail({ action: "click", ref: "e13" }, "browser")).toBe("e13");
    expect(
      toolDetail({ action: "type", ref: "e2", text: "hello" }, "browser"),
    ).toBe("hello");
  });

  it("host 按 action 出细节", () => {
    expect(
      toolDetail({ action: "shell", command: "Get-Process" }, "host"),
    ).toBe("Get-Process");
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
});

describe("toolGroupSummary · read_url", () => {
  it("uses a count title instead of URL basenames", () => {
    const tools = [
      step({
        id: "a",
        tool_name: "read_url",
        arguments: {
          url: "https://zhuanlan.zhihu.com/p/1050596771_121124370",
        },
      }),
      step({
        id: "b",
        tool_name: "read_url",
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
        tool_name: "file_read",
        arguments: { path: "src/foo.ts" },
      }),
      step({
        id: "b",
        tool_name: "file_read",
        arguments: { path: "src/bar.ts" },
      }),
    ];
    expect(toolGroupSummary(tools)).toBe("Read file foo.ts · bar.ts");
  });
});

describe("ComposingToolLine · 中文组装心跳", () => {
  it("renders 正在组装 + 字数，不留英文 Composing/chars", () => {
    renderWithTooltip(
      <ComposingToolLine tool={{ toolName: "web_search", chars: 1280 }} />,
    );
    expect(screen.getByText(/正在组装/)).toBeTruthy();
    expect(screen.getByText(/1\.3k 字/)).toBeTruthy();
    expect(screen.queryByText(/Composing/i)).toBeNull();
    expect(screen.queryByText(/chars/i)).toBeNull();
  });

  it("omits char count when zero", () => {
    renderWithTooltip(
      <ComposingToolLine tool={{ toolName: "debate", chars: 0 }} />,
    );
    expect(screen.getByText(/正在组装/)).toBeTruthy();
    expect(screen.queryByText(/字/)).toBeNull();
  });
});

describe("ToolLine · tool_use_end.failure product face", () => {
  it("shows failure.message on the collapsed row, not the technical result", () => {
    renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "web_search",
          arguments: { query: "AgentCore" },
          result:
            "搜索失败：ConnectError: [Errno 111] Connection refused to searxng.internal:8080",
          status: "error",
          failure: {
            message: "工具执行失败，请稍后重试。",
            code: "TOOL_ERROR",
          },
        })}
      />,
    );
    expect(screen.getByText("工具执行失败，请稍后重试。")).toBeTruthy();
    expect(screen.queryByText(/searxng\.internal/)).toBeNull();
    expect(screen.queryByText(/Connection refused/)).toBeNull();
  });

  it("still exposes technical result after expand", () => {
    renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "web_search",
          arguments: { query: "AgentCore" },
          result:
            "搜索失败：ConnectError: [Errno 111] Connection refused to searxng.internal:8080",
          status: "error",
          failure: {
            message: "工具执行失败，请稍后重试。",
            code: "TOOL_ERROR",
          },
        })}
      />,
    );
    fireEvent.click(screen.getByText("工具执行失败，请稍后重试。"));
    expect(screen.getByText(/searxng\.internal:8080/)).toBeTruthy();
  });

  it("falls back to result peek when failure is absent", () => {
    renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "code_execute",
          arguments: {},
          result: "ExecEnvProbeFailed: 127.0.0.1:5432",
          status: "error",
        })}
      />,
    );
    expect(screen.getByText("ExecEnvProbeFailed: 127.0.0.1:5432")).toBeTruthy();
  });

  it("surfaces failure.message even for peek-suppressed tools", () => {
    renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "file_read",
          arguments: { path: "missing.md" },
          result: "FileNotFoundError: missing.md",
          status: "error",
          failure: {
            message: "读取文件失败。",
            code: "FILE_NOT_FOUND",
          },
        })}
      />,
    );
    expect(screen.getByText("读取文件失败。")).toBeTruthy();
    expect(screen.queryByText(/FileNotFoundError/)).toBeNull();
  });
});

describe("ToolLine · file_read ceiling guidance", () => {
  it("shows warning affordance instead of fault-red ✗", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "file_read",
          arguments: { path: "doc.md" },
          result:
            "已多次读取 `doc.md`（本 run 上限 5 次）。请求范围仍在对话投影窗中，本次不重复灌入全文。请直接使用已有正文，勿再读全文。",
          status: "success",
        })}
      />,
    );
    expect(container.querySelector(".text-destructive")).toBeNull();
    expect(container.querySelector(".text-warning")).toBeTruthy();
  });

  it("keeps real file_read IO failures destructive", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "file_read",
          arguments: { path: "missing.md" },
          result: "读取文件失败：文件不存在",
          status: "error",
        })}
      />,
    );
    expect(container.querySelector(".text-destructive")).toBeTruthy();
    expect(container.querySelector(".text-warning")).toBeNull();
  });

  it("excludes ceiling guidance from tool-group failed badge", () => {
    renderWithTooltip(
      <ToolLineGroup
        tools={[
          step({
            id: "a",
            tool_name: "file_read",
            arguments: { path: "a.md" },
            result: "已多次读取 `a.md`，勿再读此文件",
            status: "error",
          }),
          step({
            id: "b",
            tool_name: "code_execute",
            arguments: {},
            result: "boom",
            status: "error",
          }),
        ]}
        isStreaming={false}
      />,
    );
    // Only the real code_execute fault counts — ceiling is guidance.
    expect(screen.getByText("1 failed")).toBeTruthy();
  });
});

describe("ToolLine · git 执行相位", () => {
  // git can sit ~2min behind the repo queue, a credential lookup and a remote round
  // trip. Each of those waits reports its own phase, so the running row must name the
  // leg instead of showing a bare pulse — and each backend token needs real copy here.
  it.each([
    ["git_queued", "Waiting for repo"],
    ["git_credentials", "Checking credentials"],
    ["git_remote", "Contacting remote"],
    ["executing", "Running"],
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

  it("degrades an unknown backend phase to the generic hint", () => {
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
    expect(screen.getByText("Working")).toBeTruthy();
  });
});

describe("ToolLine · test_run budget exceeded", () => {
  it("shows warning affordance instead of fault-red ✗", () => {
    const { container } = renderWithTooltip(
      <ToolLine
        step={step({
          tool_name: "test_run",
          arguments: { check: "typecheck" },
          result: "验证未在 300s 预算内完成（验证未完成，非工具故障）",
          status: "error",
          display: {
            check: "typecheck",
            exit_code: -1,
            stdout: "",
            stderr: "Timeout: execution exceeded 300s",
            budget_exceeded: true,
          },
        })}
      />,
    );
    expect(container.querySelector(".text-destructive")).toBeNull();
    expect(container.querySelector(".text-warning")).toBeTruthy();
    expect(container.textContent).toContain("验证未完成（预算耗尽）");
  });
});
