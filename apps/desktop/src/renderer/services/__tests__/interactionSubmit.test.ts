import { notifyError, notifyInfo } from "@/lib/toast";
import { ApiError } from "@/services/api";
import { resolveInteraction } from "@/services/interaction";
import { isPausedFrameGone, runResume } from "@/services/turns";
import { useInteractionStore } from "@/stores/interactions";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  isInteractionOrphanedError,
  isPendingInteractionsAwaitingError,
  notifySubmitInteractionResult,
  submitInteraction,
  submitInteractionFeedback,
} from "../interactionSubmit";

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifyInfo: vi.fn(),
}));

const fillMock = vi.fn();
vi.mock("@/services/interaction", () => ({
  resolveInteraction: vi.fn(),
}));
vi.mock("@/services/turns", () => ({
  runResume: vi.fn(),
  isPausedFrameGone: vi.fn(),
}));
vi.mock("@/stores/composer", () => ({
  useComposerDraftStore: {
    getState: () => ({ fill: fillMock }),
  },
}));
const clearError = vi.fn();
vi.mock("@/stores/conversation", () => ({
  useConversationStore: {
    getState: () => ({ clearError }),
  },
}));

const resolveMock = vi.mocked(resolveInteraction);
const resumeMock = vi.mocked(runResume);
const frameGoneMock = vi.mocked(isPausedFrameGone);
const store = () => useInteractionStore.getState();

beforeEach(() => {
  store().clear();
  resolveMock.mockReset();
  resumeMock.mockReset();
  frameGoneMock.mockReset();
  clearError.mockReset();
  fillMock.mockReset();
  vi.mocked(notifyError).mockReset();
  vi.mocked(notifyInfo).mockReset();
  resolveMock.mockResolvedValue("settled");
  resumeMock.mockResolvedValue(undefined);
  frameGoneMock.mockReturnValue(false);
});

describe("submitInteraction path table", () => {
  it("hot path: approval → resolveInteraction + resolved", async () => {
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      payload: { approval_id: "a1", tool_name: "x", arguments: {} },
    });
    const result = await submitInteraction({
      id: "a1",
      kind: "approval",
      conversationId: "c1",
      hotBody: { kind: "approval", decision: "approve" },
    });
    expect(result).toBe("ok");
    expect(resolveMock).toHaveBeenCalledWith(
      "c1",
      "a1",
      {
        kind: "approval",
        decision: "approve",
      },
      "cloud",
    );
    expect(store().get("a1")?.status).toBe("resolved");
  });

  it("cold path: ask_user → runResume", async () => {
    store().upsertRequired({
      kind: "ask_user",
      conversationId: "c1",
      messageId: "m1",
      payload: { checkpoint_id: "cp1", question: "q" },
    });
    const result = await submitInteraction({
      id: "cp1",
      kind: "ask_user",
      conversationId: "c1",
      cold: { messageId: "srv-m1", decision: "continue", note: "" },
    });
    expect(result).toBe("ok");
    // 会话 id 显式传下去：横幅挂/清必须落在同一条会话上（卡未必属于当前打开的会话）。
    expect(resumeMock).toHaveBeenCalledWith(
      "srv-m1",
      "continue",
      "",
      undefined,
      {
        conversationId: "c1",
      },
    );
    expect(store().get("cp1")?.status).toBe("resolved");
  });

  it("cold path after recovery: no interactions entry still calls runResume (plan_review)", async () => {
    // Recovery clears cold pending_interactions; pausedTurns is the authority.
    expect(store().get("pr1")).toBeUndefined();
    const result = await submitInteraction({
      id: "pr1",
      kind: "plan_review",
      conversationId: "c1",
      cold: {
        messageId: "srv-m1",
        decision: "continue",
        note: "先做公开竞品",
      },
    });
    expect(result).toBe("ok");
    expect(resumeMock).toHaveBeenCalledWith(
      "srv-m1",
      "continue",
      "先做公开竞品",
      undefined,
      { conversationId: "c1" },
    );
    expect(store().get("pr1")?.status).toBe("resolved");
  });

  it("cold path after recovery: ask_user without interactions entry still resumes", async () => {
    expect(store().get("cp-ask")).toBeUndefined();
    const result = await submitInteraction({
      id: "cp-ask",
      kind: "ask_user",
      conversationId: "c1",
      cold: {
        messageId: "srv-ask",
        decision: "continue",
        note: "选 A",
        selected: ["a"],
      },
    });
    expect(result).toBe("ok");
    expect(resumeMock).toHaveBeenCalledWith(
      "srv-ask",
      "continue",
      "选 A",
      ["a"],
      { conversationId: "c1" },
    );
  });

  it("cold path: runResume failure does not markResolved", async () => {
    resumeMock.mockRejectedValue(
      new Error("resume blocked: sidecar unavailable"),
    );
    await expect(
      submitInteraction({
        id: "pr1",
        kind: "plan_review",
        conversationId: "c1",
        cold: { messageId: "srv-m1", decision: "continue", note: "" },
      }),
    ).rejects.toThrow(/sidecar unavailable/);
    // Stub from markResolved must not appear — failure left no resolved entry,
    // or if a prior pending existed it would reopen. Here there was none.
    expect(store().get("pr1")).toBeUndefined();
  });

  it("cold path: tracked entry reopens on runResume failure (no fake resolved)", async () => {
    store().upsertRequired({
      kind: "plan_review",
      conversationId: "c1",
      messageId: "m1",
      payload: { checkpoint_id: "pr1", steps: [], pending: [] },
    });
    resumeMock.mockRejectedValue(
      new Error("resume blocked: sidecar probe failed"),
    );
    await expect(
      submitInteraction({
        id: "pr1",
        kind: "plan_review",
        conversationId: "c1",
        cold: { messageId: "srv-pr", decision: "continue", note: "" },
      }),
    ).rejects.toThrow(/probe failed/);
    expect(store().get("pr1")?.status).toBe("pending");
  });

  it("410 interaction_orphaned → orphaned status (no reopen)", async () => {
    store().upsertRequired({
      kind: "escalation",
      conversationId: "c1",
      messageId: "m1",
      payload: { escalation_id: "e1", question: "q", assumption: "a" },
    });
    resolveMock.mockRejectedValue(
      new ApiError(
        410,
        JSON.stringify({ detail: { code: "interaction_orphaned" } }),
      ),
    );
    const result = await submitInteraction({
      id: "e1",
      kind: "escalation",
      conversationId: "c1",
      hotBody: {
        kind: "escalation",
        answer: "",
        use_assumption: true,
        transfer_ownership: false,
      },
    });
    expect(result).toBe("orphaned");
    expect(store().get("e1")?.status).toBe("orphaned");
  });

  it("non-410 failure → reopen for retry", async () => {
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      payload: { approval_id: "a1", tool_name: "x", arguments: {} },
    });
    resolveMock.mockRejectedValue(new ApiError(500, "boom"));
    await expect(
      submitInteraction({
        id: "a1",
        kind: "approval",
        conversationId: "c1",
        hotBody: { kind: "approval", decision: "deny" },
      }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(store().get("a1")?.status).toBe("pending");
  });

  it("hot submitting guard blocks double submit", async () => {
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      payload: { approval_id: "a1", tool_name: "x", arguments: {} },
    });
    let release!: () => void;
    resolveMock.mockImplementation(
      () =>
        new Promise((r) => {
          release = () => r("settled");
        }),
    );
    const first = submitInteraction({
      id: "a1",
      kind: "approval",
      conversationId: "c1",
      hotBody: { kind: "approval", decision: "approve" },
    });
    const second = await submitInteraction({
      id: "a1",
      kind: "approval",
      conversationId: "c1",
      hotBody: { kind: "approval", decision: "approve" },
    });
    expect(second).toBe("busy");
    release();
    await first;
  });

  it("cold path does not return busy when interactions entry is absent", async () => {
    // While a cold submit is in flight, a second call must NOT get "busy"
    // (dedup is the caller's local submitting state, not interactions.beginSubmit).
    let resolveFirst!: () => void;
    const firstGate = new Promise<void>((r) => {
      resolveFirst = () => r();
    });
    resumeMock.mockImplementationOnce(() => firstGate);
    resumeMock.mockResolvedValueOnce(undefined);

    const first = submitInteraction({
      id: "pr1",
      kind: "plan_review",
      conversationId: "c1",
      cold: { messageId: "srv-m1", decision: "continue", note: "" },
    });
    const second = await submitInteraction({
      id: "pr1",
      kind: "plan_review",
      conversationId: "c1",
      cold: { messageId: "srv-m1", decision: "continue", note: "" },
    });
    expect(second).toBe("ok");
    expect(resumeMock).toHaveBeenCalledTimes(2);
    resolveFirst();
    await expect(first).resolves.toBe("ok");
  });
});

/**
 * 「这张卡已经结了」的回执（云对话多端同权 B2 · 验收 5）：卡收起来不再可点，但不认领结果
 * 与处理方——回执不带 `status` / `arbitrated_by`，证不了是人还是运行时兜底结的。
 */
describe("submitInteraction · 已经结了的回执", () => {
  function raiseApproval(id: string): void {
    store().upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      payload: { approval_id: id, tool_name: "x", arguments: {} },
    });
  }

  it("热路 200 already_processed → 收口，不再可点", async () => {
    raiseApproval("a-dup");
    resolveMock.mockResolvedValue("already_processed");

    const result = await submitInteraction({
      id: "a-dup",
      kind: "approval",
      conversationId: "c1",
      hotBody: { kind: "approval", decision: "approve" },
    });

    expect(result).toBe("already_settled");
    expect(store().get("a-dup")?.status).toBe("resolved");
    expect(store().get("a-dup")?.settledByReceipt).toBe(true);
  });

  it("热路 404 → 说「已经处理过了」，不说「卡失效了」", async () => {
    raiseApproval("a-404");
    resolveMock.mockRejectedValue(new ApiError(404, "not found"));

    const result = await submitInteraction({
      id: "a-404",
      kind: "approval",
      conversationId: "c1",
      hotBody: { kind: "approval", decision: "approve" },
    });

    expect(result).toBe("already_settled");
    expect(store().get("a-404")?.status).toBe("resolved");
    expect(submitInteractionFeedback("already_settled")).toContain(
      "已经处理过了",
    );
  });

  it("回执不认领结果：resolution 仍是空的（等线材帧）", async () => {
    raiseApproval("a-blank");
    resolveMock.mockResolvedValue("already_processed");

    await submitInteraction({
      id: "a-blank",
      kind: "approval",
      conversationId: "c1",
      hotBody: { kind: "approval", decision: "deny" },
    });

    expect(store().get("a-blank")?.resolution).toBeUndefined();
    expect(store().get("a-blank")?.settledElsewhere).toBeUndefined();
  });

  /**
   * 冷路的 404 现在只剩「诚实失效」这一种：帧被上一次续跑吃掉时服务端回 200 +
   * `resume_settled`，压根不到这条路。所以这里不能再洗成「已经处理过了」——那是替一次
   * 真失效编一个好听的结局；卡该作废（灰掉），横幅留着说清是清理还是重新生成。
   */
  it("冷路帧真失效 → 卡作废（不冒充「已处理」、也不放回可点）", async () => {
    store().upsertRequired({
      kind: "plan_review",
      conversationId: "c1",
      messageId: "m1",
      payload: { checkpoint_id: "pr-gone", steps: [], pending: [] },
    });
    resumeMock.mockRejectedValue(
      new Error("这次暂停已超过保留期被清理，无法继续"),
    );
    frameGoneMock.mockReturnValue(true);

    const result = await submitInteraction({
      id: "pr-gone",
      kind: "plan_review",
      conversationId: "c1",
      cold: { messageId: "srv-pr", decision: "continue", note: "" },
    });

    expect(result).toBe("orphaned");
    expect(store().get("pr-gone")?.status).toBe("orphaned");
    expect(store().get("pr-gone")?.settledByReceipt).toBeUndefined();
    // runResume 挂的失效横幅是这次唯一诚实的解释，不许被清掉。
    expect(clearError).not.toHaveBeenCalled();
  });

  it("冷路帧还在的失败仍放回可点（这次没发出去，不是卡结了）", async () => {
    store().upsertRequired({
      kind: "plan_review",
      conversationId: "c1",
      messageId: "m1",
      payload: { checkpoint_id: "pr-busy", steps: [], pending: [] },
    });
    resumeMock.mockRejectedValue(new ApiError(409, "busy"));

    await expect(
      submitInteraction({
        id: "pr-busy",
        kind: "plan_review",
        conversationId: "c1",
        cold: { messageId: "srv-pr", decision: "continue", note: "" },
      }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(store().get("pr-busy")?.status).toBe("pending");
    expect(clearError).not.toHaveBeenCalled();
  });
});

describe("提交没走成时的提示", () => {
  it("「已经结了」不是错——多端同权下是常态，不报红", () => {
    notifySubmitInteractionResult("already_settled");

    expect(vi.mocked(notifyInfo)).toHaveBeenCalledWith(
      submitInteractionFeedback("already_settled"),
    );
    expect(vi.mocked(notifyError)).not.toHaveBeenCalled();
  });

  it("忙 / 失效仍报错", () => {
    notifySubmitInteractionResult("busy");
    notifySubmitInteractionResult("orphaned");

    expect(vi.mocked(notifyError)).toHaveBeenCalledWith("请稍候再试");
    expect(vi.mocked(notifyError)).toHaveBeenCalledWith("确认已失效");
  });
});

describe("error helpers", () => {
  it("detects 410 orphaned from detail.code", () => {
    const err = new ApiError(
      410,
      JSON.stringify({ detail: { code: "interaction_orphaned" } }),
    );
    expect(isInteractionOrphanedError(err)).toBe(true);
  });

  it("detects 409 pending_interactions_awaiting", () => {
    const err = new ApiError(
      409,
      JSON.stringify({
        detail: {
          code: "pending_interactions_awaiting",
          pending_kinds: ["approval"],
        },
      }),
    );
    expect(isPendingInteractionsAwaitingError(err)).toBe(true);
  });
});
