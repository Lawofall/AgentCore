// @vitest-environment jsdom
import {
  ProcessLane,
  reasoningPlainPreview,
} from "@/components/chat/ProcessLane";
import type { ProcessStep } from "@agentcore/protocol-conformance";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

const tool = (
  name: string,
  id = name,
): Extract<ProcessStep, { kind: "tool" }> => ({
  kind: "tool",
  id,
  tool_name: name,
  arguments: {},
  result: null,
  status: "success",
});

describe("reasoningPlainPreview", () => {
  it("strips markdown from the first line", () => {
    expect(reasoningPlainPreview("**粗体** 与 `代码`")).toBe("粗体 与 代码");
    expect(reasoningPlainPreview("# 标题行\n第二行")).toBe("标题行");
  });
});

describe("ProcessLane", () => {
  it("shows outer summary with thought preview when collapsed", () => {
    render(
      <ProcessLane
        steps={[
          { kind: "reasoning", text: "先查资料。" },
          tool("web_search", "t1"),
        ]}
      />,
    );
    expect(screen.getByText("思考 1 步 · 使用 1 个工具")).toBeTruthy();
    expect(screen.getByText("先查资料。")).toBeTruthy();
    expect(screen.queryByText("web_search")).toBeNull();
    expect(screen.queryByRole("button", { name: /^思考$/ })).toBeNull();

    fireEvent.click(screen.getByText("思考 1 步 · 使用 1 个工具"));
    expect(screen.getByRole("button", { name: /^思考$/ })).toBeTruthy();
    expect(screen.getByText("web_search")).toBeTruthy();
    expect(screen.getByText("先查资料。")).toBeTruthy();
    expect(screen.queryByLabelText("工具参数")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^思考$/ }));
    expect(screen.getByRole("button", { name: /^思考$/ }).getAttribute("aria-expanded")).toBe(
      "true",
    );
    expect(screen.getByText("先查资料。")).toBeTruthy();
  });

  it("shows inner thought preview for a single pure thought without outer summary", () => {
    render(
      <ProcessLane
        steps={[{ kind: "reasoning", text: "完整思考" }]}
      />,
    );
    expect(screen.queryByText(/思考 \d+ 步/)).toBeNull();
    expect(screen.getByRole("button", { name: /^思考$/ })).toBeTruthy();
    expect(screen.getByText("完整思考")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /^思考$/ }));
    expect(screen.getAllByText("完整思考").length).toBeGreaterThan(0);
  });

  it("groups consecutive tools and hides names until the group opens", () => {
    render(
      <ProcessLane
        steps={[tool("grep", "g1"), tool("file_read", "f1"), tool("write", "w1")]}
      />,
    );
    expect(screen.getByText("使用 3 个工具")).toBeTruthy();
    expect(screen.queryByText("grep")).toBeNull();
    expect(screen.queryByText("file_read")).toBeNull();

    fireEvent.click(screen.getByText("使用 3 个工具"));
    const groupButtons = screen.getAllByText("使用 3 个工具");
    fireEvent.click(groupButtons[groupButtons.length - 1]!);
    expect(screen.getByText("grep")).toBeTruthy();
    expect(screen.getByText("file_read")).toBeTruthy();
    expect(screen.getByText("write")).toBeTruthy();
  });

  it("does not group a lone tool", () => {
    render(<ProcessLane steps={[tool("grep")]} />);
    expect(screen.getAllByText("使用 1 个工具").length).toBe(1);
    expect(screen.queryByText("grep")).toBeNull();
    fireEvent.click(screen.getByText("使用 1 个工具"));
    expect(screen.getByText("grep")).toBeTruthy();
  });

  it("breaks tool runs on non-tool steps", () => {
    render(
      <ProcessLane
        steps={[
          tool("a", "a1"),
          tool("b", "b1"),
          { kind: "reasoning", text: "中间思考" },
          tool("c", "c1"),
        ]}
      />,
    );
    fireEvent.click(screen.getByText("思考 1 步 · 使用 3 个工具"));
    expect(screen.getByText("使用 2 个工具")).toBeTruthy();
    expect(screen.getByText("c")).toBeTruthy();
  });

  it("defaults inner thought expanded when collapse is false", () => {
    render(
      <ProcessLane
        collapse={false}
        steps={[
          { kind: "reasoning", text: "队员过程正文" },
          tool("web_search", "t1"),
        ]}
      />,
    );
    expect(screen.queryByText(/思考 \d+ 步/)).toBeNull();
    expect(screen.getByText("队员过程正文")).toBeTruthy();
    expect(screen.getByText("web_search")).toBeTruthy();
  });

  it("marks a channel redirect as 改道, not a fault X", () => {
    render(
      <ProcessLane
        collapse={false}
        steps={[
          {
            kind: "tool",
            id: "t1",
            tool_name: "code_execute",
            arguments: {},
            result: "禁止用 code_execute 打开源码再正则扫描。",
            status: "redirect",
            failure: {
              message: "这一步想用脚本打开源码再搜索，没有执行。",
              code: "source_grep_redirect",
            },
          },
        ]}
      />,
    );
    expect(screen.getByText("改道")).toBeTruthy();
    expect(screen.queryByLabelText("error")).toBeNull();
    fireEvent.click(screen.getByText("code_execute"));
    expect(screen.getByText(/想用脚本打开源码再搜索/)).toBeTruthy();
    expect(screen.queryByText(/禁止用/)).toBeNull();
  });
});
