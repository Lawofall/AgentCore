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

    const entry = screen.getByRole("button", { name: "上下文" });
    expect(entry.textContent).toBe("上下文");
    expect(entry.textContent).not.toMatch(/段/);
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
    fireEvent.click(screen.getByRole("button", { name: "上下文" }));
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
    expect(screen.getByRole("button", { name: "上下文" }).textContent).toBe(
      "上下文",
    );
    fireEvent.click(screen.getByRole("button", { name: "上下文" }));
    expect(screen.queryByText("常驻指令")).toBeNull();
    expect(screen.queryByRole("button", { name: /输出风格/ })).toBeNull();
    expect(screen.getByRole("button", { name: /原始请求/ })).toBeTruthy();
  });

  it("defaults to the first 材料 row; source lives in the catalog, not body chips", () => {
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
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "上下文" }));
    const materialBtn = screen.getByRole("button", {
      name: /前置 · 调研员/,
    });
    expect(materialBtn.getAttribute("aria-current")).toBe("true");
    expect(materialBtn.textContent).toContain("18 字");
    expect(screen.getByTestId("received-context-body").textContent).toBe(
      "竞品 A/B/C 的定价区间……",
    );
    expect(screen.queryByText("来自 调研员")).toBeNull();
    expect(screen.queryByText("摘要")).toBeNull();
    expect(screen.getByText("已截断")).toBeTruthy();
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
    fireEvent.click(screen.getByRole("button", { name: "上下文" }));
    expect(screen.getByText("环境")).toBeTruthy();
    expect(screen.getByRole("button", { name: /团队位置/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /团队共识/ })).toBeTruthy();
  });

  it("does not badge pointer as 已截断 even when the wire stamp is true", () => {
    render(
      <ReceivedContextSection
        blocks={[
          block({
            channel: "team_result",
            body: "交接结论：已落盘。",
            source_role: "案卷作者",
            source_run_id: "run-up",
            fidelity: "pointer",
            truncated: true,
          }),
        ]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "上下文" }));
    expect(
      screen.getByRole("button", { name: /回传 · 案卷作者/ }),
    ).toBeTruthy();
    expect(screen.queryByText("来自 案卷作者")).toBeNull();
    expect(screen.queryByText("递指针")).toBeNull();
    expect(screen.queryByText("已截断")).toBeNull();
  });
});

describe("ReceivedContextDialog reader", () => {
  it("defaults to 常驻指令 even when 本回合工具 is present", () => {
    render(
      <ReceivedContextDialog
        open
        onOpenChange={() => undefined}
        blocks={[
          block({ channel: "system", body: "你是 CEO。" }),
          block({
            channel: "tools",
            body: "**web_search**\n\n- `query`: string（必填）",
          }),
          block({ channel: "request", body: "发下参数" }),
        ]}
      />,
    );
    const standingBtn = screen.getByRole("button", { name: /常驻指令/ });
    expect(standingBtn.getAttribute("aria-current")).toBe("true");
    const nav = screen.getByRole("navigation", { name: "上下文目录" });
    expect(nav.textContent?.indexOf("常驻指令")).toBeLessThan(
      nav.textContent?.indexOf("本回合工具") ?? -1,
    );
    expect(screen.getByTestId("received-context-body").textContent).toBe(
      "你是 CEO。",
    );
  });

  it("defaults to 常驻指令 when there is no 本回合工具 row", () => {
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
    const standingBtn = screen.getByRole("button", { name: /常驻指令/ });
    expect(standingBtn.getAttribute("aria-current")).toBe("true");
    expect(standingBtn.className).toContain("text-sm");
    expect(standingBtn.querySelector("span.tabular-nums")?.className).toContain(
      "text-xs",
    );
    expect(screen.getByTestId("received-context-body").textContent).toBe(
      "你是 CEO。",
    );
  });

  it("defaults to 本回合工具 and keeps it on a narrow layout", () => {
    isNarrow = true;
    render(
      <ReceivedContextDialog
        open
        onOpenChange={() => undefined}
        blocks={[
          block({ channel: "system", body: "你是 CEO。" }),
          block({
            channel: "tools",
            body: "**web_search**\n\n- `query`: string（必填）",
          }),
          block({ channel: "request", body: "发下参数" }),
        ]}
      />,
    );
    expect(screen.queryByRole("button", { name: /常驻指令/ })).toBeNull();
    const toolsBtn = screen.getByRole("button", { name: /本回合工具/ });
    expect(toolsBtn.getAttribute("aria-current")).toBe("true");
    expect(screen.getByTestId("received-context-body").textContent).toContain(
      "`query`: string（必填）",
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
