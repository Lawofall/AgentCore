// @vitest-environment jsdom
/**
 * ProcessTimeline wait chrome：展示名 Wait；成功不可展开、不泄回执/reason；
 * 失败仍可见产品句；file_delete 等仍可展开看结果。
 */
import { ProcessTimeline } from "@/components/ProcessTimeline";
import type { ProcessStep } from "@agentcore/contract-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}));

afterEach(cleanup);

const WAIT_ACK =
  "已确认等待团队事件（无需处置）。继续静默听团；勿再为等待而调用 delegate / update_synthesis。";
const WAIT_REASON = "工程实践研究员已完成，学术视角研究员仍在跑…";
const WAIT_FAIL = "当前不在协调模式——仅在协调模式启动团队后可用。";
const DELETE_RESULT = "已删除 draft.md。可从回收站恢复。";

const successWait: ProcessStep = {
  kind: "tool",
  id: "w1",
  tool_name: "wait",
  arguments: { reason: WAIT_REASON },
  result: WAIT_ACK,
  status: "success",
};

const failedWait: ProcessStep = {
  kind: "tool",
  id: "w2",
  tool_name: "wait",
  arguments: { reason: WAIT_REASON },
  result: "",
  status: "error",
  failure: { message: WAIT_FAIL, code: "wait_not_coordination" },
};

const deletedFile: ProcessStep = {
  kind: "tool",
  id: "d1",
  tool_name: "file_delete",
  arguments: { path: "draft.md" },
  result: DELETE_RESULT,
  status: "success",
};

describe("ProcessTimeline · wait chrome", () => {
  it("成功 wait 标签为 Wait，不可展开，不泄回执与 reason", () => {
    render(<ProcessTimeline steps={[successWait]} isStreaming />);

    expect(screen.getByText("Wait")).toBeTruthy();
    expect(screen.getByText("完成")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Wait/ })).toBeNull();
    expect(screen.queryByText(WAIT_ACK)).toBeNull();
    expect(screen.queryByText(/已确认等待团队事件/)).toBeNull();
    expect(screen.queryByText(WAIT_REASON)).toBeNull();
    expect(screen.queryByText(/研究员仍在跑/)).toBeNull();
    expect(screen.queryByText(/"reason"/)).toBeNull();

    fireEvent.click(screen.getByText("Wait"));
    expect(screen.queryByText(WAIT_ACK)).toBeNull();
    expect(screen.queryByText(WAIT_REASON)).toBeNull();
    expect(screen.queryByText(/"reason"/)).toBeNull();
  });

  it("失败 wait 仍可展开看产品句，不泄 reason JSON", () => {
    render(<ProcessTimeline steps={[failedWait]} isStreaming />);

    expect(screen.getByText("Wait")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
    expect(screen.queryByText(WAIT_FAIL)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Wait/ }));
    expect(screen.getByText(WAIT_FAIL)).toBeTruthy();
    expect(screen.queryByText(WAIT_REASON)).toBeNull();
    expect(screen.queryByText(/"reason"/)).toBeNull();
  });

  it("file_delete 仍可展开看结果，恢复说明不占折叠行", () => {
    render(<ProcessTimeline steps={[deletedFile]} isStreaming />);

    expect(screen.getByText("Delete file")).toBeTruthy();
    expect(screen.getByText("draft.md")).toBeTruthy();
    expect(screen.queryByText(DELETE_RESULT)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Delete file/ }));
    expect(screen.getByText(DELETE_RESULT)).toBeTruthy();
  });
});
