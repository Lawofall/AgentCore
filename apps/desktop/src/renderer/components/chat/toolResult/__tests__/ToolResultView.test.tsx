// @vitest-environment jsdom
/**
 * Render test for consult expand cards: historical consult_memory / consult_rule
 * plus unified `consult` origin badges (能力指引 / 设定 / 查阅).
 * The block comment here detaches the @vitest-environment directive from
 * the import block so organizeImports keeps it file-leading.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type ToolResultData, ToolResultView } from "../ToolResultView";
import { GENERIC_TOOL_FAILURE_MESSAGE } from "../productFailureFace";

const navigate = vi.fn();

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

afterEach(cleanup);

beforeEach(() => {
  navigate.mockClear();
});

function data(p: Partial<ToolResultData>): ToolResultData {
  return {
    toolName: "x",
    args: {},
    result: null,
    display: null,
    status: "success",
    ...p,
  };
}

describe("ToolResultView · consult_memory", () => {
  it("renders the pulled memory note as a name +「查阅记忆」badge card", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "consult_memory",
          display: { topic: "部署流程" },
          result: "## 笔记\n- 用 pnpm dev 起前端",
        })}
      />,
    );
    expect(screen.getByText("查阅记忆")).toBeTruthy();
    expect(screen.queryByText("查阅记忆：")).toBeNull();
    expect(screen.getByText("部署流程")).toBeTruthy();
    expect(screen.getByText(/用 pnpm dev 起前端/)).toBeTruthy();
  });

  it("shows the header even when the note body is empty", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "consult_memory",
          display: { topic: "项目背景" },
          result: "",
        })}
      />,
    );
    expect(screen.getByText("查阅记忆")).toBeTruthy();
    expect(screen.getByText("项目背景")).toBeTruthy();
  });
});

describe("ToolResultView · consult (unified)", () => {
  it("paints missing origin as「查阅」, never「查阅记忆」", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "consult",
          display: { name: "部署流程" },
          result: "## 笔记\n- 用 pnpm dev 起前端",
        })}
      />,
    );
    expect(screen.getByText("查阅")).toBeTruthy();
    expect(screen.queryByText("查阅记忆")).toBeNull();
    expect(screen.queryByText("查阅记忆：")).toBeNull();
    expect(screen.getByText("部署流程")).toBeTruthy();
    expect(screen.getByText(/用 pnpm dev 起前端/)).toBeTruthy();
  });

  it("paints origin=system as「能力指引」", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "consult",
          display: { name: "debate_and_review", origin: "system" },
          result: "对需对抗性多视角思考的问题用 debate。",
        })}
      />,
    );
    expect(screen.getByText("能力指引")).toBeTruthy();
    expect(screen.getByText("debate_and_review")).toBeTruthy();
    expect(screen.queryByText("查阅记忆")).toBeNull();
  });

  it("paints origin=user as「设定」", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "consult",
          display: { name: "合规附录", origin: "user" },
          result: "不得外泄客户数据",
        })}
      />,
    );
    expect(screen.getByText("设定")).toBeTruthy();
    expect(screen.getByText("合规附录")).toBeTruthy();
    expect(screen.queryByText("查阅记忆")).toBeNull();
  });

  it("does not paint resolve_folder display.name as a consult card", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "resolve_folder",
          display: {
            status: "resolved",
            folder_id: "550e8400-e29b-41d4-a716-446655440000",
            name: "白板",
            rel_path: "白板",
          },
          result: "唯一命中，可直接用于后续派工",
        })}
      />,
    );
    expect(screen.queryByText("查阅记忆")).toBeNull();
    expect(screen.queryByText("查阅记忆：")).toBeNull();
    expect(screen.queryByText("查阅")).toBeNull();
    expect(screen.queryByText("能力指引")).toBeNull();
    expect(screen.queryByText("设定")).toBeNull();
    expect(screen.getByText(/唯一命中，可直接用于后续派工/)).toBeTruthy();
  });
});

describe("ToolResultView · consult_rule (historical)", () => {
  it("maps display.rule onto the consult card with「设定」", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "consult_rule",
          display: { rule: "合规附录" },
          result: "不得外泄客户数据",
        })}
      />,
    );
    expect(screen.getByText("设定")).toBeTruthy();
    expect(screen.queryByText("查阅记忆")).toBeNull();
    expect(screen.queryByText("查阅记忆：")).toBeNull();
    expect(screen.getByText("合规附录")).toBeTruthy();
    expect(screen.getByText(/不得外泄客户数据/)).toBeTruthy();
  });
});

describe("ToolResultView · search_conversations / read_conversation", () => {
  it("renders a search card with result_count and hit-list body in result", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "search_conversations",
          display: { result_count: 2, scope: "all" },
          result: "1. 上周方案 · abc\n2. 部署讨论 · def",
        })}
      />,
    );
    expect(screen.getByText("检索对话")).toBeTruthy();
    expect(screen.getByText(/2 场/)).toBeTruthy();
    expect(screen.getByText(/上周方案/)).toBeTruthy();
    // Search cards have no conversation_id — no deep-link button.
    expect(screen.queryByRole("button", { name: "打开对话" })).toBeNull();
  });

  it("renders a read card with title, conversation_id, truncated badge, body from result", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "read_conversation",
          display: {
            title: "上周方案复盘",
            conversation_id: "conv_abc123",
            truncated: true,
            depth: "full",
          },
          result: "### User\n上次结论是什么？\n### Assistant\n采用方案 B。",
        })}
      />,
    );
    expect(screen.getByText("查阅对话：")).toBeTruthy();
    expect(screen.getByText("上周方案复盘")).toBeTruthy();
    expect(screen.getByText("conv_abc123")).toBeTruthy();
    expect(screen.getByText("已截断")).toBeTruthy();
    expect(screen.getByText(/采用方案 B/)).toBeTruthy();
  });

  it("deep-links「打开对话」to /conversations/:id when conversation_id is present", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "read_conversation",
          display: {
            title: "旧案",
            conversation_id: "conv_deeplink",
            truncated: false,
            depth: "full",
          },
          result: "### User\nhi",
        })}
      />,
    );
    const btn = screen.getByRole("button", { name: "打开对话" });
    fireEvent.click(btn);
    expect(navigate).toHaveBeenCalledWith("/conversations/conv_deeplink");
  });

  it("clips a huge transcript preview while keeping the truncated footer", () => {
    const huge = `${"x".repeat(6500)}\nTAIL_MARKER`;
    render(
      <ToolResultView
        data={data({
          toolName: "read_conversation",
          display: {
            title: "超长场",
            conversation_id: "conv_long",
            truncated: false,
          },
          result: huge,
        })}
      />,
    );
    expect(screen.getByText(/预览已截断/)).toBeTruthy();
    expect(screen.queryByText(/TAIL_MARKER/)).toBeNull();
  });
});

describe("ToolResultView · read_url", () => {
  it("renders a source-style header + body from display (not JSON result)", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "read_url",
          display: {
            url: "https://weather.example.com/sz",
            title: "深圳天气",
            site: "weather.example.com",
            snippet: "多云转晴",
            content: "今天气温 20-28 度。",
          },
          result:
            '{"url":"https://weather.example.com/sz","title":"深圳天气","content":"今天气温 20-28 度。"}',
        })}
      />,
    );
    const link = screen.getByRole("link", { name: /深圳天气/ });
    expect(link.getAttribute("href")).toBe("https://weather.example.com/sz");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(screen.getByText("weather.example.com")).toBeTruthy();
    expect(screen.getByText("今天气温 20-28 度。")).toBeTruthy();
    // Raw JSON must not appear — display is the sole render source.
    expect(screen.queryByText(/\{"url":/)).toBeNull();
  });
});

describe("ToolResultView · file_read ceiling guidance", () => {
  it("renders ceiling cheap-hit as warning, not destructive", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "file_read",
          result:
            "已多次读取 `doc.md`（本 run 上限 5 次）。请求范围仍在对话投影窗中，本次不重复灌入全文。请直接使用已有正文，勿再读全文。",
          status: "success",
        })}
      />,
    );
    const pre = container.querySelector("pre");
    expect(pre?.textContent).toContain("不重复灌入全文");
    expect(pre?.className).toContain("text-warning");
    expect(pre?.className).not.toContain("text-destructive");
  });

  it("keeps real file_read errors destructive", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "file_read",
          result: "读取文件失败：文件不存在",
          status: "error",
        })}
      />,
    );
    expect(container.querySelector("pre")?.className).toContain(
      "text-destructive",
    );
  });

  it("expanded view keeps model-facing result and hides the generic fallback", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "web_search",
          status: "error",
          result:
            "搜索失败：ConnectError: [Errno 111] Connection refused to searxng.internal:8080",
          failure: {
            message: GENERIC_TOOL_FAILURE_MESSAGE,
            code: "TOOL_ERROR",
          },
        })}
      />,
    );
    expect(container.textContent).toContain("searxng.internal:8080");
    expect(container.textContent).not.toContain(GENERIC_TOOL_FAILURE_MESSAGE);
    expect(
      container.querySelector("[data-testid=tool-product-failure]"),
    ).toBeNull();
  });

  it("expanded view shows a specific product failure above the technical result", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "browser_click",
          status: "error",
          result: "ElementNotFound: e13",
          failure: { message: "未找到元素 e13。", code: "NOT_FOUND" },
        })}
      />,
    );
    expect(container.textContent).toContain("未找到元素 e13。");
    expect(container.textContent).toContain("ElementNotFound: e13");
    expect(
      container.querySelector("[data-testid=tool-product-failure]")
        ?.textContent,
    ).toBe("未找到元素 e13。");
  });

  it("redirect shows only the user face, not the model steer", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "code_execute",
          status: "redirect",
          result:
            "禁止用 code_execute 打开源码再正则扫描（检测到：re.findall(）。",
          failure: {
            message:
              "这一步想用脚本打开源码再搜索，没有执行。我会改用搜索工具定位后再读文件。",
            code: "source_grep_redirect",
          },
        })}
      />,
    );
    expect(container.textContent).toContain("我会改用搜索工具定位后再读文件");
    expect(container.textContent).not.toContain("禁止用 code_execute");
    expect(container.querySelector("pre")).toBeNull();
    expect(container.querySelector(".text-destructive")).toBeNull();
  });
});

describe("ToolResultView · test_run budget exceeded", () => {
  it("shows incomplete banner and muted Timeout stderr (not fault red)", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "test_run",
          result: "验证未在 300s 预算内完成（验证未完成，非工具故障）",
          status: "error",
          display: {
            check: "typecheck",
            command: "npx tsc --noEmit",
            exit_code: -1,
            stdout: "",
            stderr: "Timeout: execution exceeded 300s",
            budget_exceeded: true,
          },
        })}
      />,
    );
    expect(container.textContent).toContain("验证未完成（预算耗尽）");
    const stderr = Array.from(container.querySelectorAll("pre")).find((p) =>
      p.textContent?.includes("Timeout"),
    );
    expect(stderr?.className).toContain("text-muted-foreground");
    expect(stderr?.className).not.toContain("text-destructive");
  });
});

describe("ToolResultView · code_diagnostics", () => {
  it("renders ok errors as 类型诊断 list (not budget / not fault red)", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "code_diagnostics",
          status: "success",
          display: {
            kind: "code_diagnostics",
            status: "ok",
            diagnostics: [
              {
                path: "a.ts",
                line: 12,
                column: 5,
                severity: "error",
                message: "Property 'foo' does not exist",
                code: "TS2339",
              },
            ],
          },
        })}
      />,
    );
    expect(container.textContent).toContain("类型诊断");
    expect(container.textContent).toContain("a.ts:12");
    expect(container.textContent).toContain("Property 'foo' does not exist");
    expect(container.textContent).not.toContain("验证未完成");
    expect(container.textContent).not.toContain("预算耗尽");
    expect(container.innerHTML).not.toContain("text-destructive");
  });

  it("appends diagnostics below str_replace diff", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "str_replace",
          status: "success",
          args: {
            path: "a.ts",
            old_string: "x",
            new_string: "y",
          },
          display: {
            kind: "code_diagnostics",
            status: "ok",
            diagnostics: [
              {
                path: "a.ts",
                line: 12,
                column: 5,
                severity: "error",
                message: "Property 'foo' does not exist",
              },
            ],
          },
        })}
      />,
    );
    expect(container.textContent).toContain("a.ts");
    expect(container.textContent).toContain("类型诊断");
    expect(container.textContent).toContain("Property 'foo' does not exist");
    expect(container.textContent).not.toContain("验证未完成");
  });

  it("renders unavailable with reason (neutral)", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "file_write",
          status: "success",
          args: { path: "a.ts", content: " const x = 1" },
          display: {
            kind: "code_diagnostics",
            status: "unavailable",
            reason: "LSP 未就绪",
            diagnostics: [],
          },
        })}
      />,
    );
    expect(container.textContent).toContain("类型诊断");
    expect(container.textContent).toContain("LSP 未就绪");
    expect(container.textContent).not.toContain("验证未完成");
  });

  it("renders clean ok as 未发现类型错误", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "code_diagnostics",
          status: "success",
          display: {
            kind: "code_diagnostics",
            status: "ok",
            diagnostics: [],
          },
        })}
      />,
    );
    expect(container.textContent).toContain("未发现类型错误");
  });
});
