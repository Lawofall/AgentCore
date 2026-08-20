// @vitest-environment jsdom
/**
 * Render + interaction tests for the mobile 交互式暂停放行 card (前端技术与架构 §七, AUD-012).
 *
 * PauseCard turns the conformance-folded `pendingInteraction` into the actionable buttons that
 * POST the user's decision to the live SSE (api/interaction.resolveInteraction). 挂起即收口
 * (②, Phase 3): only an `approval` resolves live in-stream now — a checkpoint (ask_user) /
 * plan_review finalizes the turn and is continued via the durable ResumeCard, so PauseCard
 * handles approvals only. These assert the per-tool button gating that mirrors the backend gate
 * (code_execute hides「本轮都允许」per PI-004; file ops add the class grant), that each click
 * submits the right discriminated body, and the error path. The block comment keeps the
 * @vitest-environment directive file-leading past organizeImports.
 */

import { resolveInteraction } from "@/api/interaction";
import { PauseCard } from "@/components/PauseCard";
import {
  __resetRemoteSettlementsForTests,
  getRemoteSettlementSnapshot,
  isLocalSettlement,
} from "@/lib/remoteSettlement";
import type { ProjectedInteraction } from "@agentcore/protocol-conformance";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/interaction", () => ({ resolveInteraction: vi.fn() }));
const mockResolve = vi.mocked(resolveInteraction);

const CONV = "conv-1";

afterEach(cleanup);
beforeEach(() => {
  mockResolve.mockReset();
  mockResolve.mockResolvedValue("settled");
  __resetRemoteSettlementsForTests();
});

function approval(
  over: Partial<Extract<ProjectedInteraction, { kind: "approval" }>> = {},
): Extract<ProjectedInteraction, { kind: "approval" }> {
  return {
    kind: "approval",
    id: "appr-1",
    status: "pending",
    toolCallId: "tc-1",
    toolName: "file_write",
    arguments: { path: "/tmp/x" },
    ...over,
  };
}

describe("PauseCard · approval", () => {
  it("renders the 中文 tool label + headline arg and the full button set for a file op", () => {
    render(<PauseCard pending={approval()} conversationId={CONV} />);
    expect(screen.getByText("Agent 请求执行 · 写入文件")).toBeTruthy();
    expect(screen.getByText("/tmp/x")).toBeTruthy();
    expect(screen.getByText("允许一次")).toBeTruthy();
    expect(screen.getByText("本轮都允许")).toBeTruthy();
    // file_write ∈ FILE_OP_TOOLS → the class grant is offered.
    expect(screen.getByText("本轮内所有文件改动")).toBeTruthy();
    expect(screen.getByText("拒绝")).toBeTruthy();
  });

  it("submits an `approve` decision to resolveInteraction with the approvalId", async () => {
    render(<PauseCard pending={approval()} conversationId={CONV} />);
    fireEvent.click(screen.getByText("允许一次"));
    await waitFor(() =>
      expect(mockResolve).toHaveBeenCalledWith(CONV, "appr-1", {
        kind: "approval",
        decision: "approve",
      }),
    );
    // On success the card stays busy until the stream's *_resolved unmounts it.
    expect(screen.getByText("处理中…")).toBeTruthy();
  });

  it("submits `deny` and `approve_always_files` from the matching buttons", async () => {
    render(<PauseCard pending={approval()} conversationId={CONV} />);
    fireEvent.click(screen.getByText("本轮内所有文件改动"));
    await waitFor(() =>
      expect(mockResolve).toHaveBeenLastCalledWith(CONV, "appr-1", {
        kind: "approval",
        decision: "approve_always_files",
      }),
    );

    cleanup();
    render(<PauseCard pending={approval()} conversationId={CONV} />);
    fireEvent.click(screen.getByText("拒绝"));
    await waitFor(() =>
      expect(mockResolve).toHaveBeenLastCalledWith(CONV, "appr-1", {
        kind: "approval",
        decision: "deny",
      }),
    );
  });

  it("shows「本轮都允许」for code_execute (Cursor-aligned turn grant)", () => {
    render(
      <PauseCard
        pending={approval({
          toolName: "code_execute",
          arguments: { command: "ls" },
        })}
        conversationId={CONV}
      />,
    );
    expect(screen.getByText("Agent 请求执行 · 执行代码")).toBeTruthy();
    expect(screen.getByText("允许一次")).toBeTruthy();
    expect(screen.getByText("本轮都允许")).toBeTruthy();
    expect(screen.getByText("拒绝")).toBeTruthy();
    // code_execute ∉ FILE_OP_TOOLS — file-class grant still hidden.
    expect(screen.queryByText("本轮内所有文件改动")).toBeNull();
  });

  it("force_one_shot: fuse copy + only 允许一次/拒绝 (no turn grants)", () => {
    render(
      <PauseCard
        pending={approval({
          // file_write ∈ FILE_OP_TOOLS — proves both turn-grant buttons hide.
          toolName: "file_write",
          arguments: {
            path: "/tmp/x",
            force_one_shot: true,
            rule_id: "destructive.workspace_top_tree",
            circuit_breaker_hint:
              "检测到疑似删除工作区顶层整项目目录的命令（启发式兜底，并非完整拦截）。",
          },
        })}
        conversationId={CONV}
      />,
    );
    expect(
      screen.getByText(/安全熔断升格审批（启发式兜底，并非完整拦截）/),
    ).toBeTruthy();
    expect(screen.getByText("允许一次")).toBeTruthy();
    expect(screen.getByText("拒绝")).toBeTruthy();
    expect(screen.queryByText("本轮都允许")).toBeNull();
    expect(screen.queryByText("本轮内所有文件改动")).toBeNull();
    expect(screen.queryByText(/敏感路径读升格审批/)).toBeNull();
  });

  it("sensitive.path_read_ask: no fuse boilerplate, turn grant + key preview", () => {
    render(
      <PauseCard
        pending={approval({
          toolName: "file_read",
          arguments: {
            path: ".env",
            rule_id: "sensitive.path_read_ask",
            circuit_breaker_hint:
              "该路径疑似凭据。\n键名预览（无值，启发式）：DATABASE_URL（共 1 个）",
          },
        })}
        conversationId={CONV}
      />,
    );
    expect(screen.getByText(/敏感路径读升格审批/)).toBeTruthy();
    expect(screen.getByText(/键名预览（无值/)).toBeTruthy();
    expect(screen.getByText(/DATABASE_URL/)).toBeTruthy();
    expect(screen.queryByText(/安全熔断升格审批/)).toBeNull();
    expect(screen.getByText("允许一次")).toBeTruthy();
    expect(screen.getByText("本轮都允许")).toBeTruthy();
    expect(screen.getByText("拒绝")).toBeTruthy();
  });

  it("does not treat bare circuit_breaker_hint as a fuse card", () => {
    render(
      <PauseCard
        pending={approval({
          toolName: "file_write",
          arguments: {
            path: "/tmp/x",
            circuit_breaker_hint: "legacy hint without machine flags",
          },
        })}
        conversationId={CONV}
      />,
    );
    expect(screen.queryByText(/安全熔断升格审批/)).toBeNull();
    expect(screen.queryByText(/敏感路径读升格审批/)).toBeNull();
    expect(screen.getByText("本轮都允许")).toBeTruthy();
    expect(screen.getByText("本轮内所有文件改动")).toBeTruthy();
  });

  it("点之前先记账，好让随后回来的 approval_resolved 认得出是自己点的", async () => {
    render(<PauseCard pending={approval()} conversationId={CONV} />);
    fireEvent.click(screen.getByText("允许一次"));
    // 记账必须早于 POST 结果——事件可能比回执先到。
    expect(isLocalSettlement("appr-1")).toBe(true);
    await waitFor(() => expect(mockResolve).toHaveBeenCalled());
    expect(getRemoteSettlementSnapshot()).toEqual([]);
  });

  it("already_processed（另一端先点了）→ 留下「已由另一端处理」而不是静默无事发生", async () => {
    mockResolve.mockResolvedValueOnce("already_processed");
    const onResolved = vi.fn();
    render(
      <PauseCard
        pending={approval()}
        conversationId={CONV}
        onResolved={onResolved}
      />,
    );
    fireEvent.click(screen.getByText("允许一次"));
    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));
    expect(getRemoteSettlementSnapshot()).toEqual([
      { interactionId: "appr-1", conversationId: CONV, kind: "approval" },
    ]);
  });

  it("host 按 action 出中文工具名与审批摘要", () => {
    render(
      <PauseCard
        pending={approval({
          toolName: "host",
          arguments: { action: "shell", command: "Get-Process" },
        })}
        conversationId={CONV}
      />,
    );
    expect(screen.getByText("Agent 请求执行 · 本机 Host")).toBeTruthy();
    expect(screen.getByText("shell Get-Process")).toBeTruthy();
  });

  it("surfaces an error and re-enables the card when the POST fails", async () => {
    mockResolve.mockRejectedValueOnce(new Error("放行失败 (500)"));
    render(<PauseCard pending={approval()} conversationId={CONV} />);
    fireEvent.click(screen.getByText("允许一次"));
    expect(await screen.findByText("放行失败 (500)")).toBeTruthy();
    // busy cleared on failure → no「处理中…」, the buttons are clickable again.
    expect(screen.queryByText("处理中…")).toBeNull();
  });
});
