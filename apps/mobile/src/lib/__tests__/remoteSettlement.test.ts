/**
 * 「已由另一端处理」记账（云对话多端同权 B2 · P1 · 验收 5）。
 *
 * 锁四条：① 收口事件不带处理方 → 归属只能靠本端记账；② 只给用户真看得见的卡立墓碑
 *（否则整段重放会把历史上每次放行都弹一遍）；③ 事件名 → kind/id 走契约表，不硬编码；
 * ④ 无人参与的收口（主管仲裁 / 按假设 / 超时）不许算到用户头上。
 */
import {
  __resetRemoteSettlementsForTests,
  answeredByAPerson,
  dismissRemoteSettlement,
  getRemoteSettlementSnapshot,
  interactionLabel,
  isForeignSettlement,
  isLocalSettlement,
  markLocalSettlement,
  noteRemoteSettlement,
  noteRemoteSettlementFromReceipt,
  receiptProvesAPerson,
  settlementFromResolvedEvent,
  unmarkLocalSettlement,
} from "@/lib/remoteSettlement";
import { afterEach, describe, expect, it } from "vitest";

afterEach(() => {
  __resetRemoteSettlementsForTests();
});

const VISIBLE = (...ids: string[]): ReadonlySet<string> => new Set(ids);

describe("settlementFromResolvedEvent · 契约表反查", () => {
  it("认出热路 / 冷路 / 推进卡的收口事件并取出各自的 id 字段", () => {
    expect(
      settlementFromResolvedEvent("approval_resolved", {
        approval_id: "appr-1",
        decision: "approve",
      }),
    ).toEqual({ kind: "approval", interactionId: "appr-1" });
    expect(
      settlementFromResolvedEvent("checkpoint_resolved", {
        checkpoint_id: "cp-1",
      }),
    ).toEqual({ kind: "ask_user", interactionId: "cp-1" });
    expect(
      settlementFromResolvedEvent("stage_card_resolved", {
        stage_card_id: "sc-1",
      }),
    ).toEqual({ kind: "stage_card", interactionId: "sc-1" });
    expect(
      settlementFromResolvedEvent("escalation_resolved", {
        escalation_id: "esc-1",
        status: "resolved",
      }),
    ).toEqual({ kind: "escalation", interactionId: "esc-1" });
  });

  it("非收口事件 / 缺 id → null（`*_required` 与正文帧一律不算）", () => {
    expect(
      settlementFromResolvedEvent("approval_required", { approval_id: "a" }),
    ).toBeNull();
    expect(settlementFromResolvedEvent("content_delta", { delta: "x" })).toBe(
      null,
    );
    expect(settlementFromResolvedEvent("approval_resolved", {})).toBeNull();
    expect(settlementFromResolvedEvent("approval_resolved", undefined)).toBe(
      null,
    );
  });

  it("无人参与的升级收口 → null（不经这里就没人能立墓碑）", () => {
    for (const payload of [
      { escalation_id: "esc-1", status: "assumed" },
      { escalation_id: "esc-1", status: "timed_out" },
      { escalation_id: "esc-1", status: "orphaned" },
      { escalation_id: "esc-1", status: "resolved", arbitrated_by: "ceo" },
    ]) {
      expect(settlementFromResolvedEvent("escalation_resolved", payload)).toBe(
        null,
      );
    }
  });
});

describe("answeredByAPerson · 无人参与的收口", () => {
  it("升级卡：只有 status=resolved 且 arbitrated_by 缺省才算人答的", () => {
    // 经典用户直答路径缺省这个字段（契约原话）；有值即走了仲裁通道。
    expect(answeredByAPerson("escalation", { status: "resolved" })).toBe(true);
    expect(
      answeredByAPerson("escalation", {
        status: "resolved",
        answer: "走 B",
      }),
    ).toBe(true);
  });

  it("按假设 / 超时 / 失效 = 运行时兜底，没有人参与", () => {
    expect(answeredByAPerson("escalation", { status: "assumed" })).toBe(false);
    expect(answeredByAPerson("escalation", { status: "timed_out" })).toBe(
      false,
    );
    expect(answeredByAPerson("escalation", { status: "orphaned" })).toBe(false);
    expect(answeredByAPerson("escalation", undefined)).toBe(false);
    expect(answeredByAPerson("escalation", {})).toBe(false);
  });

  it("仲裁通道不算这张卡被人拍了（含 via_user：人答的是主管的问，不是这张卡）", () => {
    for (const arbitrated_by of ["ceo", "user"]) {
      expect(
        answeredByAPerson("escalation", { status: "resolved", arbitrated_by }),
      ).toBe(false);
    }
    expect(
      answeredByAPerson("escalation", {
        status: "resolved",
        arbitrated_by: "ceo",
        via_user: true,
      }),
    ).toBe(false);
  });

  it("其余 kind 的收口今天只有「人答了」一个生产者 → 不设这道闸", () => {
    for (const kind of [
      "approval",
      "ask_user",
      "plan_review",
      "team_preview",
      "stage_card",
    ]) {
      expect(answeredByAPerson(kind, { status: "assumed" })).toBe(true);
      expect(answeredByAPerson(kind, undefined)).toBe(true);
    }
  });
});

describe("receiptProvesAPerson · REST 回执证不了升级卡", () => {
  it("回执不带 status / arbitrated_by → 升级卡一律证不了", () => {
    expect(receiptProvesAPerson("escalation")).toBe(false);
  });

  it("其余 kind 的回执可信", () => {
    expect(receiptProvesAPerson("approval")).toBe(true);
    expect(receiptProvesAPerson("ask_user")).toBe(true);
    expect(receiptProvesAPerson("stage_card")).toBe(true);
  });

  it("noteRemoteSettlementFromReceipt 拒收升级卡并如实返回 false", () => {
    expect(
      noteRemoteSettlementFromReceipt({
        interactionId: "esc-1",
        conversationId: "c1",
        kind: "escalation",
      }),
    ).toBe(false);
    expect(getRemoteSettlementSnapshot()).toEqual([]);

    expect(
      noteRemoteSettlementFromReceipt({
        interactionId: "appr-1",
        conversationId: "c1",
        kind: "approval",
      }),
    ).toBe(true);
    expect(getRemoteSettlementSnapshot()).toHaveLength(1);
  });
});

describe("isForeignSettlement · 归属判定", () => {
  it("本端点过的不算外来（记账早于 POST，抢先回来的事件也认得出）", () => {
    markLocalSettlement("appr-1");
    expect(isLocalSettlement("appr-1")).toBe(true);
    expect(isForeignSettlement("appr-1", VISIBLE("appr-1"))).toBe(false);
  });

  it("没点过 + 卡正摆着 = 另一端点的", () => {
    expect(isForeignSettlement("appr-2", VISIBLE("appr-2"))).toBe(true);
  });

  it("卡没露过面（重放段 required+resolved 同批到达）→ 不立墓碑", () => {
    expect(isForeignSettlement("appr-3", VISIBLE())).toBe(false);
  });

  it("空 id 不算", () => {
    expect(isForeignSettlement("", VISIBLE(""))).toBe(false);
  });

  it("撤回登记后归属交回线材帧（回执说没结成，本端这一点不算数）", () => {
    markLocalSettlement("esc-1");
    unmarkLocalSettlement("esc-1");
    expect(isLocalSettlement("esc-1")).toBe(false);
    expect(isForeignSettlement("esc-1", VISIBLE("esc-1"))).toBe(true);
  });
});

describe("提示条存储", () => {
  it("同一张卡只留一条（事件与 REST 回执可能各报一次）", () => {
    const entry = {
      interactionId: "appr-1",
      conversationId: "c1",
      kind: "approval",
    };
    noteRemoteSettlement(entry);
    noteRemoteSettlement({ ...entry, kind: "stage_card" });
    expect(getRemoteSettlementSnapshot()).toEqual([entry]);
  });

  it("「知道了」收走一条；缺 id / 缺会话的帧直接丢弃", () => {
    noteRemoteSettlement({
      interactionId: "appr-1",
      conversationId: "c1",
      kind: "approval",
    });
    noteRemoteSettlement({
      interactionId: "",
      conversationId: "c1",
      kind: "approval",
    });
    noteRemoteSettlement({
      interactionId: "appr-2",
      conversationId: "",
      kind: "approval",
    });
    expect(getRemoteSettlementSnapshot()).toHaveLength(1);
    dismissRemoteSettlement("appr-1");
    expect(getRemoteSettlementSnapshot()).toEqual([]);
  });

  it("切会话清空：提示条与记账一起作废", () => {
    markLocalSettlement("appr-1");
    noteRemoteSettlement({
      interactionId: "appr-9",
      conversationId: "c1",
      kind: "approval",
    });
    __resetRemoteSettlementsForTests();
    expect(getRemoteSettlementSnapshot()).toEqual([]);
    expect(isLocalSettlement("appr-1")).toBe(false);
  });
});

describe("interactionLabel", () => {
  it("各 kind 有自己的卡面名，未知 kind 有兜底", () => {
    expect(interactionLabel("approval")).toBe("工具审批");
    expect(interactionLabel("plan_review")).toBe("计划复核");
    expect(interactionLabel("team_preview")).toBe("开工确认");
    expect(interactionLabel("ask_user")).toBe("提问确认");
    expect(interactionLabel("escalation")).toBe("拍板请求");
    expect(interactionLabel("stage_card")).toBe("推进卡");
    expect(interactionLabel("what_is_this")).toBe("确认");
  });
});
