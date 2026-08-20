// @vitest-environment jsdom
/**
 * 卡被另一端拍板 → 决策区留一条收口，而不是**直接消失**（云对话多端同权 B2 · 验收 2）。
 */
import { SettledElsewhereNotices } from "@/components/chat/SettledElsewhereNotices";
import { useConversationStore } from "@/stores/conversation";
import {
  INTERACTION_CARD_NAME,
  applyInteractionWireEvent,
  useInteractionStore,
} from "@/stores/interactions";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const CID = "conv-settled";
const MID = "msg-1";

function raise(id: string): void {
  act(() => {
    applyInteractionWireEvent(
      "approval_required",
      { approval_id: id, tool_name: "terminal", arguments: {} },
      CID,
      MID,
      "server",
      { live: true },
    );
  });
}

function resolveRemotely(id: string): void {
  act(() => {
    applyInteractionWireEvent(
      "approval_resolved",
      { approval_id: id, decision: "approve" },
      CID,
      MID,
      "server",
      { live: true },
    );
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  useInteractionStore.setState({ byId: new Map() });
  useConversationStore.setState({ currentConversationId: CID });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  useInteractionStore.setState({ byId: new Map() });
  useConversationStore.setState({ currentConversationId: null });
});

describe("SettledElsewhereNotices", () => {
  it("另一端拍板 → 原位收口为「已由另一端处理」，一段时间后自行退场", () => {
    render(<SettledElsewhereNotices />);
    raise("a1");
    expect(screen.queryByTestId("settled-elsewhere")).toBeNull();

    resolveRemotely("a1");

    expect(screen.getByText("已由另一端处理")).toBeTruthy();
    expect(screen.getByText(/工具审批/)).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(9_000);
    });
    expect(screen.queryByTestId("settled-elsewhere")).toBeNull();
  });

  it("本端自己点的不出收口条（用户知道是自己点的）", () => {
    render(<SettledElsewhereNotices />);
    raise("a2");
    act(() => {
      useInteractionStore.getState().beginSubmit("a2");
    });
    resolveRemotely("a2");

    expect(screen.queryByTestId("settled-elsewhere")).toBeNull();
  });

  it("别的会话的卡不串门", () => {
    render(<SettledElsewhereNotices />);
    act(() => {
      applyInteractionWireEvent(
        "approval_required",
        { approval_id: "a3", tool_name: "terminal", arguments: {} },
        "other-conv",
        MID,
        "server",
        { live: true },
      );
      applyInteractionWireEvent(
        "approval_resolved",
        { approval_id: "a3", decision: "approve" },
        "other-conv",
        MID,
        "server",
        { live: true },
      );
    });

    expect(screen.queryByTestId("settled-elsewhere")).toBeNull();
  });

  it("空快照留桩：三个 kind 出「已由另一端处理」，卡名走 INTERACTION_CARD_NAME", () => {
    render(<SettledElsewhereNotices />);
    act(() => {
      useInteractionStore.getState().upsertRequired({
        kind: "approval",
        conversationId: CID,
        messageId: MID,
        origin: "server",
        payload: { approval_id: "a-h", tool_name: "", arguments: {} },
      });
      useInteractionStore.getState().upsertRequired({
        kind: "escalation",
        conversationId: CID,
        messageId: MID,
        origin: "server",
        payload: {
          escalation_id: "e-h",
          question: "q",
          assumption: "a",
        },
      });
      useInteractionStore.getState().upsertRequired({
        kind: "stage_card",
        conversationId: CID,
        messageId: MID,
        origin: "server",
        payload: {
          stage_card_id: "sc-h",
          motion: "是否开辩",
          sides: [],
          form: "debate",
        },
      });
      useInteractionStore.getState().hydratePending(CID, [], {
        confirmed: ["server"],
      });
    });

    expect(screen.getAllByText("已由另一端处理")).toHaveLength(3);
    expect(screen.getByText(INTERACTION_CARD_NAME.approval)).toBeTruthy();
    expect(screen.getByText(INTERACTION_CARD_NAME.escalation)).toBeTruthy();
    expect(screen.getByText(INTERACTION_CARD_NAME.stage_card)).toBeTruthy();
  });
});
