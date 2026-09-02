// @vitest-environment jsdom

import {
  ReceivedContextDialog,
  ReceivedContextSection,
} from "@/components/chat/ReceivedContext";
import type { ContextBlockWire } from "@/types/events";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let isNarrow = false;

vi.mock("@/lib/narrowLayout", () => ({
  useNarrowLayoutState: () => ({
    isNarrow,
    hideChrome: false,
    conversationDrawerOpen: false,
    setConversationDrawerOpen: () => undefined,
  }),
}));

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => (
    <pre data-testid="prompt-body">{content}</pre>
  ),
}));

function block(
  overrides: Partial<ContextBlockWire> & Pick<ContextBlockWire, "channel">,
): ContextBlockWire {
  return {
    heading: "heading",
    body: "首行摘要\n完整正文第二行",
    chars: 20,
    truncated: false,
    files: [],
    source_role: "",
    source_run_id: "",
    fidelity: "",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
});

beforeEach(() => {
  isNarrow = false;
});

describe("ReceivedContextSection reader", () => {
  it("shows a single dock entry; click opens the dialog defaulting to request", () => {
    render(
      <ReceivedContextSection
        blocks={[
          block({ channel: "history", body: "昨天的对话" }),
          block({
            channel: "request",
            body: "调研主流竞品的定价并给出建议。",
            chars: 16,
          }),
          block({ channel: "team_position", body: "你是撰写员。" }),
        ]}
      />,
    );

    const entry = screen.getByRole("button", { name: /收到的上下文/ });
    expect(entry.textContent).toContain("3 段");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("button", { name: /原始请求/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /对话历史/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /团队位置/ })).toBeNull();
    expect(screen.queryByText("本回合")).toBeNull();
    expect(screen.queryByText("此前对话")).toBeNull();
    expect(screen.queryByText("环境")).toBeNull();
    expect(screen.queryByText("常驻指令")).toBeNull();
    expect(screen.queryByTestId("received-context-body")).toBeNull();

    fireEvent.click(entry);
    expect(screen.getByRole("dialog", { name: "收到的上下文" })).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: /原始请求/ })
        .getAttribute("aria-current"),
    ).toBe("true");
    expect(screen.getByTestId("received-context-body").textContent).toBe(
      "调研主流竞品的定价并给出建议。",
    );
  });

  it("opens the shared dialog with structured 常驻指令 sections", () => {
    const system = `你是 CEO。

<output_style>
- 不用 emoji
</output_style>

<tool_use>
并行调用独立工具。
</tool_use>`;
    render(
      <ReceivedContextSection
        blocks={[
          block({ channel: "system", body: system, chars: system.length }),
          block({ channel: "request", body: "帮我润色这段话。" }),
        ]}
      />,
    );

    expect(screen.queryByRole("button", { name: /常驻指令/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /收到的上下文/ }));
    expect(screen.getByRole("button", { name: /常驻指令/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /输出风格/ })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /常驻指令/ }));
    const body = screen.getByTestId("received-context-body").textContent ?? "";
    expect(body).toContain("你是 CEO。");
    expect(body).toContain("- 不用 emoji");
    expect(body).toContain("并行调用独立工具。");
  });

  it("hides 常驻指令 on a narrow layout", () => {
    isNarrow = true;
    render(
      <ReceivedContextSection
        blocks={[
          block({
            channel: "system",
            body: "<output_style>hidden</output_style>",
          }),
          block({ channel: "request", body: "窄屏请求" }),
        ]}
      />,
    );
    expect(
      screen.getByRole("button", { name: /收到的上下文/ }).textContent,
    ).toContain("1 段");
    fireEvent.click(screen.getByRole("button", { name: /收到的上下文/ }));
    expect(screen.queryByText("常驻指令")).toBeNull();
    expect(screen.queryByRole("button", { name: /输出风格/ })).toBeNull();
    expect(screen.getByRole("button", { name: /原始请求/ })).toBeTruthy();
  });

  it("defaults to the first 材料 row and opens with provenance", () => {
    const onNavigate = vi.fn();
    render(
      <ReceivedContextSection
        blocks={[
          block({ channel: "request", body: "团队目标" }),
          block({
            channel: "dependency",
            body: "竞品 A/B/C 的定价区间……",
            source_role: "调研员",
            source_run_id: "run-up",
            fidelity: "summarize",
            truncated: true,
            chars: 18,
          }),
        ]}
        onNavigate={onNavigate}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /收到的上下文/ }));
    const materialBtn = screen.getByRole("button", {
      name: /前置结果 · 调研员/,
    });
    expect(materialBtn.getAttribute("aria-current")).toBe("true");
    expect(materialBtn.textContent).toContain("18 字");
    expect(screen.getByTestId("received-context-body").textContent).toBe(
      "竞品 A/B/C 的定价区间……",
    );
    expect(screen.getByText("来自 调研员")).toBeTruthy();
    expect(screen.getByText("摘要")).toBeTruthy();
    expect(screen.getByText("已截断")).toBeTruthy();

    fireEvent.click(screen.getByText("来自 调研员"));
    expect(onNavigate).toHaveBeenCalledWith("run-up");
  });

  it("shows 环境 group label only inside the dialog", () => {
    render(
      <ReceivedContextSection
        blocks={[
          block({ channel: "team_position", body: "你是撰写员。" }),
          block({ channel: "team_brief", body: "本回合共识。" }),
        ]}
      />,
    );
    expect(screen.queryByText("环境")).toBeNull();
    expect(screen.queryByRole("button", { name: /团队位置/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /收到的上下文/ }));
    expect(screen.getByText("环境")).toBeTruthy();
    expect(screen.getByRole("button", { name: /团队位置/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /团队共识/ })).toBeTruthy();
  });

  it("source without run id degrades to a plain badge", () => {
    const onNavigate = vi.fn();
    render(
      <ReceivedContextSection
        blocks={[
          block({
            channel: "history",
            source_role: "用户",
            source_run_id: "",
            body: "用户：你好\nCEO：您好",
          }),
        ]}
        onNavigate={onNavigate}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /收到的上下文/ }));
    expect(screen.getByText("来自 用户")).toBeTruthy();
    fireEvent.click(screen.getByText("来自 用户"));
    expect(onNavigate).not.toHaveBeenCalled();
  });
});

describe("ReceivedContextDialog reader", () => {
  it("defaults to request in the shared reader shell", () => {
    render(
      <ReceivedContextDialog
        open
        onOpenChange={() => undefined}
        blocks={[
          block({ channel: "system", body: "你是 CEO。" }),
          block({ channel: "request", body: "调研竞品定价并给建议。" }),
        ]}
      />,
    );

    expect(screen.getByRole("dialog", { name: "收到的上下文" })).toBeTruthy();
    const requestBtn = screen.getByRole("button", { name: /原始请求/ });
    expect(requestBtn.getAttribute("aria-current")).toBe("true");
    expect(requestBtn.className).toContain("text-sm");
    expect(requestBtn.querySelector("span.tabular-nums")?.className).toContain(
      "text-xs",
    );
    expect(screen.getByTestId("received-context-body").textContent).toBe(
      "调研竞品定价并给建议。",
    );
  });

  it("does not repeat team_result files already inlined in the body", () => {
    render(
      <ReceivedContextDialog
        open
        onOpenChange={() => undefined}
        blocks={[
          block({
            channel: "team_result",
            heading: "工程与数据层工程师（completed）",
            body: "交接结论：骨架已落盘。\n\n> 文件产出（路径已核）：`src/lib/store.ts`",
            files: ["src/lib/store.ts", "package.json"],
            source_role: "工程与数据层工程师",
            source_run_id: "run-boot",
          }),
        ]}
        preferMaterial
      />,
    );
    expect(screen.queryByTestId("received-context-files")).toBeNull();
    expect(screen.getByTestId("received-context-body").textContent).toContain(
      "src/lib/store.ts",
    );
  });

  it("lists dependency files under the body", () => {
    render(
      <ReceivedContextDialog
        open
        onOpenChange={() => undefined}
        blocks={[
          block({
            channel: "dependency",
            heading: "前置结果（来自 骨架）",
            body: "digest",
            files: ["src/lib/store.ts"],
            source_role: "骨架",
            source_run_id: "run-up",
            fidelity: "pointer",
          }),
        ]}
        preferMaterial
      />,
    );
    expect(screen.getByTestId("received-context-files").textContent).toContain(
      "src/lib/store.ts",
    );
  });
});
