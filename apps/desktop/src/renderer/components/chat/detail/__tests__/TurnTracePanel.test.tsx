// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/developerTrace", () => ({
  fetchDeveloperTrace: vi.fn(),
  fetchCallWindow: vi.fn(),
}));

import {
  fetchCallWindow,
  fetchDeveloperTrace,
} from "@/services/developerTrace";
import { TurnTracePanel } from "../TurnTracePanel";

const fetchTrace = vi.mocked(fetchDeveloperTrace);
const fetchWindow = vi.mocked(fetchCallWindow);

afterEach(() => {
  cleanup();
  fetchTrace.mockReset();
  fetchWindow.mockReset();
});

describe("这一回合", () => {
  it("点开一条才看见原文，工具送回留在写下它的那一行", async () => {
    fetchTrace.mockResolvedValue({
      trace_id: "trace-abc",
      where: "云端",
      model: "demo",
      duration_ms: 2000,
      in_progress: false,
      lines: [
        {
          id: "j-1",
          kind: "llm_call",
          ts: "2026-10-08T07:00:01Z",
          mark: "cap round=0",
          run_id: "cap",
          round_idx: 0,
          payload: {
            content: "先看文件",
            reasoning_content: "想一下",
            tool_calls: [
              {
                function: { name: "read", arguments: '{"path":"a.txt"}' },
              },
            ],
          },
        },
        {
          id: "j-2",
          kind: "tool_call",
          mark: "read",
          payload: { name: "read", result: "FULL TEXT" },
        },
      ],
    });

    render(<TurnTracePanel conversationId="c1" messageId="m1" />);
    expect(await screen.findByText("云端 · demo · 2 秒")).toBeTruthy();
    expect(screen.getByText("trace-abc")).toBeTruthy();
    expect(screen.getByRole("button", { name: "复制编号" })).toBeTruthy();
    expect(screen.queryByText("FULL TEXT")).toBeNull();
    expect(screen.queryByText("收到")).toBeNull();
    expect(screen.queryByText("输出")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "tool_call read" }));
    expect(screen.getByText(/FULL TEXT/)).toBeTruthy();
    expect(screen.queryByText("先看文件")).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: /llm_call cap round=0/ }),
    );
    expect(screen.getByText(/先看文件/)).toBeTruthy();
    expect(screen.getByText(/想一下/)).toBeTruthy();
    expect(screen.getByText(/a.txt/)).toBeTruthy();
  });

  it("窗口打出这一次现折的消息 JSON", async () => {
    fetchTrace.mockResolvedValue({
      trace_id: "t",
      where: "本机",
      model: null,
      duration_ms: 800,
      in_progress: true,
      lines: [
        {
          id: "j-0",
          kind: "llm_call",
          mark: "w1 round=1",
          run_id: "w1",
          round_idx: 1,
          payload: { content: "继续" },
        },
      ],
    });
    fetchWindow.mockResolvedValue({
      run_id: "w1",
      available: true,
      new_from: 0,
      compacted: true,
      history_omitted: false,
      messages: [
        { role: "assistant", content: "早前步骤的摘要" },
        { role: "user", content: "从最近的工作继续" },
      ],
    });

    render(<TurnTracePanel conversationId="c1" messageId="m1" />);
    expect(await screen.findByText("本机 · 800 毫秒 · 至今")).toBeTruthy();
    expect(screen.queryByText("早前步骤的摘要")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "窗口" }));
    expect(await screen.findByText(/早前步骤的摘要/)).toBeTruthy();
    expect(screen.getByText(/从最近的工作继续/)).toBeTruthy();
    expect(screen.getByText(/"compacted": true/)).toBeTruthy();
    expect(screen.queryByText("收到")).toBeNull();
    expect(screen.queryByText("此前若干轮收成一段摘要")).toBeNull();
    expect(fetchWindow).toHaveBeenCalledWith("c1", "m1", "w1", 1);
  });

  it("记录不在时只留一句说明", async () => {
    fetchTrace.mockResolvedValue({
      trace_id: "a".repeat(32),
      where: "云端",
      duration_ms: null,
      in_progress: false,
      gap: "这次的记录不在",
      lines: [],
    });

    render(<TurnTracePanel conversationId="c1" messageId="m1" />);
    expect(await screen.findByText("这次的记录不在")).toBeTruthy();
    expect(screen.getByText("云端")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "窗口" })).toBeNull();
    expect(screen.queryByText("原始记录")).toBeNull();
  });
});
