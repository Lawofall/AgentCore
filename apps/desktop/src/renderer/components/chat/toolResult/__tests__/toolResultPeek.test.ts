import { describe, expect, it } from "vitest";
import {
  type ToolResultData,
  fileReadTitleStat,
  hasToolResultBody,
  toolResultPeek,
  writeFamilyTitleStat,
} from "../ToolResultView";

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

describe("toolResultPeek", () => {
  it("does not peek web_search hit counts onto the collapsed title", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "web_search",
          display: { query: "q", results: [{}, {}] },
          result: "2 results",
        }),
      ),
    ).toBe("");
    expect(
      toolResultPeek(
        data({
          toolName: "web_search",
          display: { query: "q", results: [] },
          result: "No results",
        }),
      ),
    ).toBe("");
  });

  it("summarizes a web_fetch as「标题 · 域名」", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "web_fetch",
          display: {
            url: "https://weather.example.com/sz",
            title: "深圳天气",
            site: "weather.example.com",
            snippet: "多云转晴",
            content: "正文内容…",
          },
          // Model-facing JSON must NOT leak into the peek.
          result:
            '{"url":"https://weather.example.com/sz","title":"深圳天气","content":"正文内容…"}',
        }),
      ),
    ).toBe("深圳天气 · weather.example.com");
  });

  it("does not peek the exit code — it lives on the title tail", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "code_execute",
          display: { stdout: "", stderr: "boom", exit_code: 1 },
        }),
      ),
    ).toBe("");
  });

  it("does not peek stdout on successful code_execute", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "code_execute",
          display: { stdout: "hello\nworld", stderr: "", exit_code: 0 },
        }),
      ),
    ).toBe("");
  });

  it("shows 验证未完成 when budget_exceeded has no timeout_kind", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "test_run",
          status: "error",
          display: {
            check: "typecheck",
            stdout: "",
            stderr: "timeout",
            exit_code: -1,
            budget_exceeded: true,
          },
        }),
      ),
    ).toBe("验证未完成");
  });

  it("shows idle hang face for timeout_kind idle", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "test_run",
          status: "error",
          display: {
            check: "typecheck",
            stdout: "",
            stderr: "idle timeout",
            exit_code: -1,
            budget_exceeded: true,
            timeout_kind: "idle",
          },
        }),
      ),
    ).toBe("执行无响应（无输出已中止）");
  });

  it("shows disaster wall face for timeout_kind disaster", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "test_run",
          status: "error",
          display: {
            check: "typecheck",
            stdout: "",
            stderr: "forced stop",
            exit_code: -1,
            budget_exceeded: true,
            timeout_kind: "disaster",
          },
        }),
      ),
    ).toBe("执行已强制中止");
  });

  it("names the path for an edit", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "edit",
          args: { file_path: "a.ts", old_string: "x", new_string: "y" },
        }),
      ),
    ).toBe("已编辑 a.ts");
  });

  it("names the path for a write", () => {
    expect(
      toolResultPeek(
        data({ toolName: "write", args: { file_path: "a.ts", content: "x" } }),
      ),
    ).toBe("已写入 a.ts");
  });

  it("names the topic for a consult_memory", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "consult_memory",
          display: { topic: "部署流程" },
          result: "## 笔记\n- x",
        }),
      ),
    ).toBe("部署流程");
  });

  it("names the entry for unified consult (display.name)", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "consult",
          display: { name: "部署流程", origin: "user" },
          result: "## 笔记\n- x",
        }),
      ),
    ).toBe("部署流程");
  });

  it("does not peek list_folders counts onto the collapsed title", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "list_folders",
          display: { count: 3 },
          result: "共 3 个文件夹：\n[...]",
        }),
      ),
    ).toBe("");
    expect(
      toolResultPeek(data({ toolName: "list_folders", display: { count: 1 } })),
    ).toBe("");
    expect(
      toolResultPeek(data({ toolName: "list_folders", display: { count: 0 } })),
    ).toBe("");
  });

  it("does not treat folder-command display.name as consult peek", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "resolve_folder",
          display: {
            status: "resolved",
            folder_id: "550e8400-e29b-41d4-a716-446655440000",
            name: "白板",
            rel_path: "白板",
          },
          result: "唯一命中，可直接用于后续派工：\n{}",
        }),
      ),
    ).not.toBe("白板");
    expect(
      toolResultPeek(
        data({
          toolName: "create_folder",
          display: {
            status: "created",
            folder_id: "550e8400-e29b-41d4-a716-446655440000",
            name: "白板",
            rel_path: "白板",
          },
          result: "已创建云文件夹",
        }),
      ),
    ).not.toBe("白板");
    expect(
      toolResultPeek(
        data({
          toolName: "delete_folder",
          display: {
            status: "deleted",
            folder_id: "550e8400-e29b-41d4-a716-446655440000",
            name: "白板",
            rel_path: "白板",
          },
          result: "已删除文件夹「白板」",
        }),
      ),
    ).not.toBe("白板");
  });

  it("names the rule for historical consult_rule", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "consult_rule",
          display: { rule: "合规附录" },
          result: "…",
        }),
      ),
    ).toBe("合规附录");
  });

  it("does not peek search_conversations counts onto the collapsed title", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "search_conversations",
          display: { result_count: 3, scope: "project" },
          result: "找到 3 场对话（scope=project）：",
        }),
      ),
    ).toBe("");
    expect(
      toolResultPeek(
        data({
          toolName: "search_conversations",
          display: { result_count: 0, scope: "all" },
          result: "未找到可查阅的历史对话",
        }),
      ),
    ).toBe("");
  });

  it("names the title for a read_conversation (with leftover 截断 mark)", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "read_conversation",
          display: {
            title: "上周方案",
            conversation_id: "c1",
            truncated: true,
          },
          result: "### User\n…",
        }),
      ),
    ).toBe("上周方案 · 截断");
  });

  it("successful handoff peeks arguments.summary, never the protocol receipt", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "handoff",
          args: { summary: "交叉验证完成，建议一周内表态" },
          result: "已收尾。",
        }),
      ),
    ).toBe("交叉验证完成，建议一周内表态");
  });

  it("does not peek grep summaries onto the collapsed title", () => {
    expect(
      toolResultPeek(data({ toolName: "grep", result: "match line\nmore" })),
    ).toBe("");
    expect(
      toolResultPeek(
        data({
          toolName: "grep",
          result:
            "1 处匹配，分布在 1 个文件中（/include_usage/）\nsrc/a.ts:1: include_usage",
        }),
      ),
    ).toBe("");
    expect(
      toolResultPeek(
        data({ toolName: "grep", result: "3 个文件匹配 /foo/\na.ts: 2" }),
      ),
    ).toBe("");
    expect(
      toolResultPeek(
        data({
          toolName: "grep",
          result:
            "本次 grep 未匹配 /Nope/。不要据此断定代码不存在。可执行下一步：① 收窄",
        }),
      ),
    ).toBe("");
  });

  it("does not peek failure.message on error (collapsed stays one line)", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "web_search",
          status: "error",
          result:
            "搜索失败：ConnectError: [Errno 111] Connection refused to searxng.internal:8080",
          failure: {
            message: "未找到元素 e13。",
            code: "NOT_FOUND",
          },
        }),
      ),
    ).toBe("");
  });

  it("does not peek model-facing result on error when failure is absent", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "host_shell",
          status: "error",
          result: "ExecEnvProbeFailed: 127.0.0.1:5432",
        }),
      ),
    ).toBe("");
  });

  it("summarizes code_diagnostics as N 个类型错误", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "edit",
          args: { file_path: "a.ts", old_string: "x", new_string: "y" },
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
              {
                path: "a.ts",
                line: 2,
                column: 1,
                severity: "error",
                message: "boom2",
              },
            ],
          },
        }),
      ),
    ).toBe("2 个类型错误");
  });

  it("keeps 已写入 path when diagnostics are clean", () => {
    expect(
      toolResultPeek(
        data({
          toolName: "write",
          args: { file_path: "a.ts", content: "x" },
          display: {
            kind: "code_diagnostics",
            status: "ok",
            diagnostics: [],
          },
        }),
      ),
    ).toBe("已写入 a.ts");
  });
});

describe("hasToolResultBody", () => {
  it("stays closed while running when nothing has arrived", () => {
    expect(hasToolResultBody(data({ status: "running", result: null }))).toBe(
      false,
    );
  });

  it("opens a running shell once the command is on the call", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "run",
          status: "running",
          args: { command: "pnpm test" },
          result: null,
        }),
      ),
    ).toBe(true);
  });

  it("opens while running once result text is present", () => {
    expect(hasToolResultBody(data({ status: "running", result: "x" }))).toBe(
      true,
    );
  });

  it("is true when a rich display is present", () => {
    expect(
      hasToolResultBody(
        data({ toolName: "web_search", display: { query: "q", results: [] } }),
      ),
    ).toBe(true);
  });

  it("is true for a write derived from its content arg", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "write",
          args: { file_path: "a", content: "x" },
          result: null,
        }),
      ),
    ).toBe(true);
  });

  it("is false for an empty text result", () => {
    expect(hasToolResultBody(data({ toolName: "grep", result: "  " }))).toBe(
      false,
    );
  });

  it("is true for a specific failure face even when result is empty", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "replan",
          status: "error",
          result: "",
          failure: { message: "计划不合法。", code: "invalid_plan" },
        }),
      ),
    ).toBe(true);
  });

  it("is true for a read miss so the row can expand", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "read",
          status: "error",
          result: "文件不存在：missing.md",
          failure: {
            message: "没找到 missing.md，我会换个方式继续。",
            code: "not_found",
          },
        }),
      ),
    ).toBe(true);
  });

  it("is false for the generic failure fallback with an empty result", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "grep",
          status: "error",
          result: "  ",
          failure: {
            message: "这一步没能完成，我会换个方式继续。",
            code: "TOOL_ERROR",
          },
        }),
      ),
    ).toBe(false);
  });

  it("successful handoff is expandable only when the brief has details", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "handoff",
          args: { summary: "只写了结论" },
          result: "已收尾。",
        }),
      ),
    ).toBe(false);
    expect(
      hasToolResultBody(
        data({
          toolName: "handoff",
          args: {
            summary: "交叉验证完成",
            key_points: ["共识：一周内需清晰立场"],
          },
          result: "已收尾。",
        }),
      ),
    ).toBe(true);
  });

  it("consult with only a name display is not expandable", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "consult",
          display: { name: "部署流程", origin: "user" },
          result: "",
        }),
      ),
    ).toBe(false);
  });

  it("consult with a result body is expandable", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "consult",
          display: { name: "部署流程" },
          result: "笔记正文",
        }),
      ),
    ).toBe(true);
  });

  it("read_conversation with only metadata is not expandable（打开在标题行）", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "read_conversation",
          display: {
            title: "上周方案",
            conversation_id: "c1",
            truncated: true,
          },
          result: "",
        }),
      ),
    ).toBe(false);
  });

  it("read_conversation with a transcript is expandable", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "read_conversation",
          display: {
            title: "上周方案",
            conversation_id: "c1",
            truncated: true,
          },
          result: "### User\n上次结论",
        }),
      ),
    ).toBe(true);
  });

  it("historical consult_skill with only a summary is expandable", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "consult_skill",
          display: { skill_name: "x", summary: "一行说明" },
          result: "",
        }),
      ),
    ).toBe(true);
  });

  it("clean code_diagnostics is not expandable", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "code_diagnostics",
          display: {
            kind: "code_diagnostics",
            status: "ok",
            diagnostics: [],
          },
        }),
      ),
    ).toBe(false);
  });

  it("code_diagnostics with errors is expandable", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "code_diagnostics",
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
        }),
      ),
    ).toBe(true);
  });

  it("browser display is expandable when the title chip hid the url", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "browser",
          display: {
            kind: "browser",
            action: "navigate",
            url: "https://example.com",
            title: "示例站",
            detail: "打开 https://example.com",
          },
        }),
      ),
    ).toBe(true);
  });

  it("browser display is not expandable when the title chip is already the url", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "browser",
          display: {
            kind: "browser",
            action: "navigate",
            url: "https://example.com",
            detail: "打开 https://example.com",
          },
        }),
      ),
    ).toBe(false);
  });

  it("browser display with a key-frame is expandable", () => {
    expect(
      hasToolResultBody(
        data({
          toolName: "browser",
          display: {
            kind: "browser",
            action: "navigate",
            url: "https://example.com",
            title: "示例站",
            frame: "browser/step-0001.jpg",
          },
        }),
      ),
    ).toBe(true);
  });
});

describe("writeFamilyTitleStat", () => {
  it("returns +/- counts for a finished edit", () => {
    expect(
      writeFamilyTitleStat(
        data({
          toolName: "edit",
          args: { file_path: "a.ts", old_string: "x", new_string: "y" },
        }),
      ),
    ).toEqual({ kind: "diff", adds: 1, dels: 1 });
  });

  it("is null while edit is still running", () => {
    expect(
      writeFamilyTitleStat(
        data({
          toolName: "edit",
          status: "running",
          args: { file_path: "a.ts", old_string: "x", new_string: "y" },
        }),
      ),
    ).toBeNull();
  });

  it("is null when the edit is a no-op", () => {
    expect(
      writeFamilyTitleStat(
        data({
          toolName: "edit",
          args: { file_path: "a.ts", old_string: "x", new_string: "x" },
        }),
      ),
    ).toBeNull();
  });

  it("returns the line count for a finished write", () => {
    expect(
      writeFamilyTitleStat(
        data({
          toolName: "write",
          args: { file_path: "a.ts", content: "one\ntwo" },
        }),
      ),
    ).toEqual({ kind: "lines", lines: 2 });
  });
});

describe("fileReadTitleStat", () => {
  it("returns a window for a truncated read", () => {
    expect(
      fileReadTitleStat(
        data({
          toolName: "read",
          args: { file_path: "a.ts" },
          result: "body\n\n（第 1–200 行，共 242 行）",
        }),
      ),
    ).toEqual({ kind: "readWindow", start: 1, end: 200, total: 242 });
  });

  it("is null for a full-file read", () => {
    expect(
      fileReadTitleStat(
        data({
          toolName: "read",
          args: { file_path: "a.ts" },
          result: "body\n\n（全文 242 行）",
        }),
      ),
    ).toBeNull();
  });

  it("is null while read is still running", () => {
    expect(
      fileReadTitleStat(
        data({
          toolName: "read",
          status: "running",
          args: { file_path: "a.ts" },
          result: "body\n\n（第 1–200 行，共 242 行）",
        }),
      ),
    ).toBeNull();
  });
});
