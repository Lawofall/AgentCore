// @vitest-environment jsdom
import { useInteractionStore } from "@/stores/interactions";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ApprovalTrace, StageCardTrace } from "../HotDecisionTrace";

describe("StageCardTrace", () => {
  beforeEach(() => {
    useInteractionStore.setState({ byId: new Map() });
  });

  afterEach(() => {
    cleanup();
  });

  it("renders resolved start_debate outcome", () => {
    const store = useInteractionStore.getState();
    store.upsertRequired({
      kind: "stage_card",
      conversationId: "c1",
      messageId: "m1",
      payload: { stage_card_id: "sc1", motion: "命题" },
    });
    store.markResolved({
      kind: "stage_card",
      id: "sc1",
      resolution: { decision: "start_debate" },
    });
    render(<StageCardTrace stageCardId="sc1" />);
    expect(screen.getByTestId("stage-card-trace").textContent).toContain(
      "推进卡 · 已开辩",
    );
  });

  it("renders orphaned outcome", () => {
    const store = useInteractionStore.getState();
    store.upsertRequired({
      kind: "stage_card",
      conversationId: "c1",
      messageId: "m1",
      payload: { stage_card_id: "sc2", motion: "命题" },
    });
    store.markOrphaned("sc2", { kind: "stage_card" });
    render(<StageCardTrace stageCardId="sc2" />);
    expect(screen.getByTestId("stage-card-trace").textContent).toContain(
      "推进卡 · 已失效",
    );
  });

  it("hides pending leftover (not a debate entry)", () => {
    useInteractionStore.getState().upsertRequired({
      kind: "stage_card",
      conversationId: "c1",
      messageId: "m1",
      payload: { stage_card_id: "sc3", motion: "命题" },
    });
    const { container } = render(<StageCardTrace stageCardId="sc3" />);
    expect(container.textContent).toBe("");
  });

  it("另一端拍的补一句归属——回看时才知道不是自己点的", () => {
    const store = useInteractionStore.getState();
    store.upsertRequired({
      kind: "stage_card",
      conversationId: "c1",
      messageId: "m1",
      payload: { stage_card_id: "sc4", motion: "命题" },
    });
    store.markResolved({
      kind: "stage_card",
      id: "sc4",
      resolution: { decision: "start_debate" },
      settledElsewhere: true,
    });
    render(<StageCardTrace stageCardId="sc4" />);
    expect(screen.getByTestId("stage-card-trace").textContent).toContain(
      "推进卡 · 已开辩 · 已由另一端处理",
    );
  });

  it("回执关掉、结果那帧还没到 → 只说「已处理」，不替它猜", () => {
    const store = useInteractionStore.getState();
    store.upsertRequired({
      kind: "stage_card",
      conversationId: "c1",
      messageId: "m1",
      payload: { stage_card_id: "sc5", motion: "命题" },
    });
    store.markSettledByReceipt({ kind: "stage_card", id: "sc5" });
    render(<StageCardTrace stageCardId="sc5" />);
    expect(screen.getByTestId("stage-card-trace").textContent).toContain(
      "推进卡 · 已处理",
    );
  });
});

describe("ApprovalTrace", () => {
  beforeEach(() => {
    useInteractionStore.setState({ byId: new Map() });
  });

  afterEach(() => {
    cleanup();
  });

  it("回执关掉的审批不说「已批准」——那是替用户认领一个决定", () => {
    const store = useInteractionStore.getState();
    store.upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      payload: { approval_id: "ap1", tool_name: "terminal", arguments: {} },
    });
    store.markSettledByReceipt({ kind: "approval", id: "ap1" });
    const { container } = render(<ApprovalTrace approvalId="ap1" />);
    expect(container.textContent).toContain("已处理");
    expect(container.textContent).not.toContain("已批准");
  });

  it("结果那帧到了就换成真的那句", () => {
    const store = useInteractionStore.getState();
    store.upsertRequired({
      kind: "approval",
      conversationId: "c1",
      messageId: "m1",
      payload: { approval_id: "ap2", tool_name: "terminal", arguments: {} },
    });
    store.markSettledByReceipt({ kind: "approval", id: "ap2" });
    store.markResolved({
      kind: "approval",
      id: "ap2",
      resolution: { decision: "deny" },
      settledElsewhere: true,
    });
    const { container } = render(<ApprovalTrace approvalId="ap2" />);
    expect(container.textContent).toContain("已拒绝");
    expect(container.textContent).toContain("已由另一端处理");
  });
});
