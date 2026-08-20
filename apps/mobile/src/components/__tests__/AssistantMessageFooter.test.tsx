// @vitest-environment jsdom
import {
  AssistantMessageFooter,
  FinishReasonChip,
} from "@/components/AssistantMessageFooter";
import type { ContextBlockWire } from "@agentcore/contract-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/InteractionSheet", () => ({
  InteractionSheet: ({
    title,
    children,
    onCollapse,
    footer,
  }: {
    title: string;
    children: React.ReactNode;
    onCollapse: () => void;
    footer: React.ReactNode;
  }) => (
    <div data-testid="interaction-sheet" data-title={title}>
      {children}
      <div>{footer}</div>
      <button type="button" onClick={onCollapse}>
        close-mock
      </button>
    </div>
  ),
}));

vi.mock("@/components/Modal", () => ({
  Modal: ({
    children,
    label,
  }: {
    children: React.ReactNode;
    label?: string;
  }) => (
    <div data-testid="copy-mode-sheet" data-label={label}>
      {children}
    </div>
  ),
}));

afterEach(cleanup);

describe("FinishReasonChip", () => {
  it("renders abnormal finish reasons", () => {
    render(<FinishReasonChip reason="degraded" />);
    expect(screen.getByTestId("finish-reason-chip").textContent).toBe(
      "空响应收尾",
    );
  });

  it("uses diagnosis label for degraded without 降级完成 prefix", () => {
    render(
      <FinishReasonChip reason="degraded" diagnosisLabel="模型返回空内容" />,
    );
    expect(screen.getByTestId("finish-reason-chip").textContent).toBe(
      "模型返回空内容",
    );
  });

  it("hides normal finishes", () => {
    const { container } = render(<FinishReasonChip reason="end_turn" />);
    expect(container.textContent).toBe("");
  });

  it("never paints hard-failure error as a top chip", () => {
    const { container } = render(<FinishReasonChip reason="error" />);
    expect(container.textContent).toBe("");
    expect(screen.queryByTestId("finish-reason-chip")).toBeNull();
  });
});

describe("AssistantMessageFooter", () => {
  it("opens copy Action Sheet with 仅交付 / 含过程 when process exists", () => {
    render(
      <AssistantMessageFooter
        content="答案"
        process={[
          {
            kind: "tool",
            id: "t1",
            tool_name: "web_search",
            arguments: {},
            result: null,
            status: "success",
          },
        ]}
        usage={{
          input: 1200,
          output: 300,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 1200,
        }}
        costText="¥0.01"
        durationMs={45000}
      />,
    );
    expect(
      screen.getByTestId("assistant-footer-copy").getAttribute("aria-label"),
    ).toBe("复制");
    expect(screen.queryByText("含过程")).toBeNull();
    fireEvent.click(screen.getByTestId("assistant-footer-copy"));
    expect(screen.getByTestId("copy-mode-sheet")).toBeTruthy();
    expect(screen.getByTestId("copy-mode-deliverable").textContent).toBe(
      "仅交付",
    );
    expect(screen.getByTestId("copy-mode-with-process").textContent).toBe(
      "含过程",
    );
    expect(screen.getByTestId("assistant-usage-summary").textContent).toBe(
      "¥0.01 · 用时 45s",
    );
    expect(
      screen.getByTestId("assistant-usage-summary").textContent,
    ).not.toMatch(/[↑↓]/);
  });

  it("opens Sheet for usage detail via ⋯", () => {
    render(
      <AssistantMessageFooter
        content="答案"
        usage={{
          input: 100,
          output: 50,
          reasoning: 10,
          cache_hit: 20,
          cache_miss: 80,
        }}
      />,
    );
    expect(screen.queryByTestId("assistant-usage-summary")).toBeNull();
    const more = screen.getByTestId("assistant-footer-more");
    expect(more.getAttribute("aria-label")).toBe("更多");
    fireEvent.click(more);
    expect(screen.getByTestId("interaction-sheet")).toBeTruthy();
    expect(screen.getByText("用量详情")).toBeTruthy();
    expect(screen.getByText("思考")).toBeTruthy();
  });

  it("omitted cache split (0/0 with input) shows billing口径, not 0 命中", () => {
    render(
      <AssistantMessageFooter
        content="答案"
        usage={{
          input: 800,
          output: 40,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 0,
        }}
      />,
    );
    fireEvent.click(screen.getByTestId("assistant-footer-more"));
    const billed = screen.getByText("按未命中计价");
    expect(billed.parentElement?.textContent).toContain("800");
    expect(screen.queryByText("缓存命中")).toBeNull();
    expect(screen.queryByText("缓存未命中")).toBeNull();
  });

  it("DeepSeek true 0 hit (miss=input) keeps miss count and still bills as miss", () => {
    render(
      <AssistantMessageFooter
        content="答案"
        usage={{
          input: 800,
          output: 40,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 800,
        }}
      />,
    );
    fireEvent.click(screen.getByTestId("assistant-footer-more"));
    const billed = screen.getByText("按未命中计价");
    expect(billed.parentElement?.textContent).toContain("800");
    expect(screen.queryByText("缓存命中")).toBeNull();
  });

  it("streaming footer is copy-only", () => {
    render(<AssistantMessageFooter content="streaming…" isStreaming />);
    expect(
      screen.getByTestId("assistant-footer-copy").getAttribute("aria-label"),
    ).toBe("复制");
    expect(screen.queryByTestId("assistant-usage-summary")).toBeNull();
    expect(screen.queryByTestId("assistant-footer-more")).toBeNull();
  });

  it("shows 收到的上下文 in 更多 with full length including system", () => {
    const blocks: ContextBlockWire[] = [
      {
        channel: "system",
        heading: "",
        body: "SYS",
        chars: 3,
        truncated: false,
        source_role: "",
        source_run_id: "",
        fidelity: "",
        files: [],
      },
      {
        channel: "request",
        heading: "目标",
        body: "做个登录页",
        chars: 5,
        truncated: false,
        source_role: "",
        source_run_id: "",
        fidelity: "",
        files: [],
      },
    ];
    render(<AssistantMessageFooter content="答案" captainContext={blocks} />);
    fireEvent.click(screen.getByTestId("assistant-footer-more"));
    expect(screen.getByTestId("received-context-menu-item").textContent).toBe(
      "收到的上下文 · 2 段",
    );
    fireEvent.click(screen.getByTestId("received-context-menu-item"));
    expect(
      screen.getByTestId("interaction-sheet").getAttribute("data-title"),
    ).toBe("收到的上下文");
    expect(screen.getByTestId("received-context-blocks")).toBeTruthy();
    expect(screen.getByText("系统提示")).toBeTruthy();
    expect(screen.getByText("SYS")).toBeTruthy();
    expect(screen.getByText("原始请求")).toBeTruthy();
    expect(screen.getByText("做个登录页")).toBeTruthy();
  });

  it("omits 收到的上下文 menu item without captainContext", () => {
    render(
      <AssistantMessageFooter
        content="答案"
        usage={{
          input: 100,
          output: 50,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 100,
        }}
      />,
    );
    fireEvent.click(screen.getByTestId("assistant-footer-more"));
    expect(screen.queryByTestId("received-context-menu-item")).toBeNull();
    expect(screen.getByText("用量详情")).toBeTruthy();
  });
});
