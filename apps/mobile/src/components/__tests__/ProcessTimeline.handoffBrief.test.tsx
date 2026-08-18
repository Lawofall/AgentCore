// @vitest-environment jsdom
/**
 * ProcessTimeline 若出现 handoff：成功行即简报卡；失败仍是错误工具行。
 */
import { ProcessTimeline } from "@/components/ProcessTimeline";
import type { ProcessStep } from "@agentcore/contract-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}));

afterEach(cleanup);

const HANDOFF_ACK = "已收尾并提交交接简报。";

const successHandoff: ProcessStep = {
  kind: "tool",
  id: "h1",
  tool_name: "handoff",
  arguments: {
    summary: "交叉验证完成",
    key_points: ["共识：一周内需清晰立场"],
    motion_card: { motion: "该不该立刻开辩", form: "debate" },
  },
  result: HANDOFF_ACK,
  status: "success",
};

const failedHandoff: ProcessStep = {
  kind: "tool",
  id: "h2",
  tool_name: "handoff",
  arguments: { summary: "交叉验证完成" },
  result: "简报校验失败",
  status: "error",
};

describe("ProcessTimeline · handoff 简报卡", () => {
  it("成功 handoff 是简报卡：peek=summary，不露 JSON / 协议回执", () => {
    render(<ProcessTimeline steps={[successHandoff]} isStreaming />);

    expect(screen.getByText("Handoff")).toBeTruthy();
    expect(screen.getByText("交叉验证完成")).toBeTruthy();
    expect(screen.queryByText(HANDOFF_ACK)).toBeNull();
    expect(screen.queryByText(/"summary"/)).toBeNull();
    expect(screen.queryByText("Done")).toBeNull();
    expect(screen.queryByText("命题卡")).toBeNull();
  });

  it("有详情可展开，不补命题卡", () => {
    render(<ProcessTimeline steps={[successHandoff]} isStreaming />);
    fireEvent.click(screen.getByRole("button", { name: /Handoff/ }));
    expect(screen.getByText("关键要点")).toBeTruthy();
    expect(screen.getByText("共识：一周内需清晰立场")).toBeTruthy();
    expect(screen.queryByText("该不该立刻开辩")).toBeNull();
  });

  it("失败 handoff 保持错误行", () => {
    render(<ProcessTimeline steps={[failedHandoff]} isStreaming />);
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.getByText("Handoff")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Handoff/ }));
    expect(screen.getByText("简报校验失败")).toBeTruthy();
  });
});
