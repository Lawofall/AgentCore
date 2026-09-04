// @vitest-environment jsdom
/**
 * Render test for consult expand cards: historical consult_memory / consult_rule
 * plus unified `consult`. Entry name / origin badge live on the ToolLine;
 * expand is body-only.
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
  it("renders the pulled memory note as body-only (name stays on the ToolLine)", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "consult_memory",
          display: { topic: "部署流程" },
          result: "## 笔记\n- 用 pnpm dev 起前端",
        })}
      />,
    );
    expect(screen.queryByText("查阅记忆")).toBeNull();
    expect(screen.queryByText("查阅记忆：")).toBeNull();
    expect(screen.queryByText("部署流程")).toBeNull();
    expect(screen.getByText(/用 pnpm dev 起前端/)).toBeTruthy();
  });

  it("renders nothing when the note body is empty", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "consult_memory",
          display: { topic: "项目背景" },
          result: "",
        })}
      />,
    );
    expect(container.textContent).toBe("");
  });
});

describe("ToolResultView · consult (unified)", () => {
  it("renders the body without repeating the entry name or origin badge", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "consult",
          display: { name: "部署流程" },
          result: "## 笔记\n- 用 pnpm dev 起前端",
        })}
      />,
    );
    expect(screen.queryByText("查阅")).toBeNull();
    expect(screen.queryByText("查阅记忆")).toBeNull();
    expect(screen.queryByText("查阅记忆：")).toBeNull();
    expect(screen.queryByText("部署流程")).toBeNull();
    expect(screen.getByText(/用 pnpm dev 起前端/)).toBeTruthy();
  });

  it("does not paint origin=system as an expand-card badge", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "consult",
          display: { name: "debate_and_review", origin: "system" },
          result: "对需对抗性多视角思考的问题用 debate。",
        })}
      />,
    );
    expect(screen.queryByText("能力指引")).toBeNull();
    expect(screen.queryByText("debate_and_review")).toBeNull();
    expect(
      screen.getByText(/对需对抗性多视角思考的问题用 debate/),
    ).toBeTruthy();
  });

  it("does not paint origin=user as an expand-card badge", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "consult",
          display: { name: "合规附录", origin: "user" },
          result: "不得外泄客户数据",
        })}
      />,
    );
    expect(screen.queryByText("设定")).toBeNull();
    expect(screen.queryByText("合规附录")).toBeNull();
    expect(screen.getByText(/不得外泄客户数据/)).toBeTruthy();
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
  it("renders the rule body without repeating the name or「设定」badge", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "consult_rule",
          display: { rule: "合规附录" },
          result: "不得外泄客户数据",
        })}
      />,
    );
    expect(screen.queryByText("设定")).toBeNull();
    expect(screen.queryByText("查阅记忆")).toBeNull();
    expect(screen.queryByText("查阅记忆：")).toBeNull();
    expect(screen.queryByText("合规附录")).toBeNull();
    expect(screen.getByText(/不得外泄客户数据/)).toBeTruthy();
  });
});

describe("ToolResultView · consult_skill (historical)", () => {
  it("renders summary + body without repeating the skill name", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "consult_skill",
          display: {
            skill_name: "debate_and_review",
            summary: "对需对抗性多视角思考的问题用 debate。",
          },
          result: "完整能力指引正文…",
        })}
      />,
    );
    expect(screen.queryByText("能力指引")).toBeNull();
    expect(screen.queryByText("debate_and_review")).toBeNull();
    expect(
      screen.getByText("对需对抗性多视角思考的问题用 debate。"),
    ).toBeTruthy();
    expect(screen.getByText(/完整能力指引正文/)).toBeTruthy();
  });
});

describe("ToolResultView · search_conversations / read_conversation", () => {
  it("renders a search hit-list body without repeating the verb or count", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "search_conversations",
          display: { result_count: 2, scope: "all" },
          result: "1. 上周方案 · abc\n2. 部署讨论 · def",
        })}
      />,
    );
    expect(screen.queryByText("检索对话")).toBeNull();
    expect(screen.queryByText(/2 场/)).toBeNull();
    expect(screen.getByText(/上周方案/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "打开对话" })).toBeNull();
  });

  it("renders a read body with id + open, without repeating the title", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "read_conversation",
          display: {
            title: "上周方案复盘",
            conversation_id: "conv_abc123",
            truncated: true,
            depth: "dialogue",
          },
          result: "### User\n上次结论是什么？\n### Assistant\n采用方案 B。",
        })}
      />,
    );
    expect(screen.queryByText("查阅对话：")).toBeNull();
    expect(screen.queryByText("上周方案复盘")).toBeNull();
    expect(screen.queryByText("已截断")).toBeNull();
    expect(screen.getByText("conv_abc123")).toBeTruthy();
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
            depth: "dialogue",
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

describe("ToolResultView · error / redirect faces", () => {
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

describe("ToolResultView · test_run incomplete", () => {
  it("expand is output-only; incomplete face stays on the ToolLine", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "test_run",
          result: "验证未取得完整结果（已中止）",
          status: "error",
          display: {
            check: "typecheck",
            command: "npx tsc --noEmit",
            exit_code: -1,
            stdout: "",
            stderr: "Timeout: no timeout_kind",
            budget_exceeded: true,
          },
        })}
      />,
    );
    expect(container.textContent).not.toContain("验证未完成");
    expect(container.textContent).not.toContain("typecheck");
    expect(container.textContent).not.toContain("预算耗尽");
    const stderr = Array.from(container.querySelectorAll("pre")).find((p) =>
      p.textContent?.includes("Timeout"),
    );
    expect(stderr?.className).toContain("text-muted-foreground");
    expect(stderr?.className).not.toContain("text-destructive");
  });

  it("idle hang expand keeps stderr muted, not the ToolLine face", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "test_run",
          result: "执行长时间无输出，已按挂起中止",
          status: "error",
          display: {
            check: "typecheck",
            command: "npx tsc --noEmit",
            exit_code: -1,
            stdout: "",
            stderr: "idle timeout",
            budget_exceeded: true,
            timeout_kind: "idle",
          },
        })}
      />,
    );
    expect(container.textContent).not.toContain("执行无响应（无输出已中止）");
    expect(container.textContent).not.toContain("预算耗尽");
    expect(container.querySelector(".text-destructive")).toBeNull();
    expect(screen.getByText(/idle timeout/)).toBeTruthy();
  });

  it("disaster wall expand keeps stderr muted, not the ToolLine face", () => {
    const { container } = render(
      <ToolResultView
        data={data({
          toolName: "test_run",
          result: "已跑满灾难顶，强制中止",
          status: "error",
          display: {
            check: "typecheck",
            command: "npx tsc --noEmit",
            exit_code: -1,
            stdout: "",
            stderr: "forced stop",
            budget_exceeded: true,
            timeout_kind: "disaster",
          },
        })}
      />,
    );
    expect(container.textContent).not.toContain("执行已强制中止");
    expect(container.textContent).not.toContain("预算耗尽");
    expect(container.querySelector(".text-destructive")).toBeNull();
    expect(screen.getByText(/forced stop/)).toBeTruthy();
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
    expect(container.textContent).not.toContain("类型诊断");
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
    expect(container.textContent).not.toContain("类型诊断");
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
    expect(container.textContent).not.toContain("类型诊断");
    expect(container.textContent).toContain("LSP 未就绪");
    expect(container.textContent).not.toContain("验证未完成");
  });

  it("renders clean ok as empty expand (face lives on the ToolLine)", () => {
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
    expect(container.textContent).toBe("");
  });
});

describe("ToolResultView · write-family cards have no path header", () => {
  it("renders a str_replace diff without repeating the path or +/-", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "str_replace",
          args: {
            path: "src/lib/store.ts",
            old_string: "a",
            new_string: "b",
          },
        })}
      />,
    );
    expect(screen.queryByText("src/lib/store.ts")).toBeNull();
    expect(screen.queryByText("+1")).toBeNull();
    expect(screen.queryByText("-1")).toBeNull();
    expect(screen.getByText("a")).toBeTruthy();
    expect(screen.getByText("b")).toBeTruthy();
  });

  it("renders a file_write preview without path / 字 chrome", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "file_write",
          args: { path: "src/new.ts", content: "export const x = 1" },
        })}
      />,
    );
    expect(screen.queryByText("src/new.ts")).toBeNull();
    expect(screen.queryByText(/行/)).toBeNull();
    expect(screen.queryByText(/字/)).toBeNull();
    expect(screen.getByText("export const x = 1")).toBeTruthy();
  });
});

describe("ToolResultView · file_read strips the line-window footer", () => {
  it("keeps the body and drops the window footer", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "file_read",
          args: { path: "src/ui/PropertyPanel.tsx" },
          result: "191| const x = 1\n\n（第 1–200 行，共 242 行）",
        })}
      />,
    );
    expect(screen.getByText(/191\| const x = 1/)).toBeTruthy();
    expect(screen.queryByText(/共 242 行/)).toBeNull();
    expect(screen.queryByText(/第 1/)).toBeNull();
  });

  it("keeps a following PDF page HOW after stripping the line footer", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "file_read",
          args: { path: "doc.pdf" },
          result:
            "extracted\n\n（第 1–200 行，共 500 行）\n\n抽取第 1–3 页，共 12 页。后面的页请用 start_page=4 再读",
        })}
      />,
    );
    expect(screen.getByText(/extracted/)).toBeTruthy();
    expect(screen.getByText(/抽取第 1–3 页/)).toBeTruthy();
    expect(screen.queryByText(/共 500 行/)).toBeNull();
  });
});

describe("ToolResultView · host", () => {
  it("renders status from display.body, not the model JSON", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "host",
          result: '{"info":{"ok":true}}',
          display: {
            kind: "host",
            action: "status",
            body: '{\n  "hostname": "DESKTOP-1"\n}',
          },
        })}
      />,
    );
    expect(screen.getByText(/DESKTOP-1/)).toBeTruthy();
    expect(screen.queryByText(/"info"/)).toBeNull();
  });

  it("renders shell via the terminal card", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "host",
          result: '{"exit_code":0,"stdout":"ok"}',
          display: {
            stdout: "listed logs",
            stderr: "",
            exit_code: 0,
            language: "host",
          },
        })}
      />,
    );
    expect(screen.getByText("listed logs")).toBeTruthy();
    expect(screen.getByText(/退出码 0/)).toBeTruthy();
    expect(screen.queryByText(/不可信内容/)).toBeNull();
  });

  it("strips historical untrusted XML when display is absent", () => {
    render(
      <ToolResultView
        data={data({
          toolName: "host",
          result: '<不可信内容>\n{"ok":true}\n</不可信内容>',
        })}
      />,
    );
    expect(screen.getByText(/"ok":true/)).toBeTruthy();
    expect(screen.queryByText(/不可信内容/)).toBeNull();
  });
});
