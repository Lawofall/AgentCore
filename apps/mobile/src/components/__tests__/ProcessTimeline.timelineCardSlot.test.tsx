// @vitest-environment jsdom
/**
 * 时间线空槽两条路径：有意为空 vs 卡片实体缺失。
 * 以前都返回 null，卡丢失时界面看起来正常，测试也无法断言这一故障。
 */
import { ProcessTimeline } from "@/components/ProcessTimeline";
import {
  TIMELINE_MISSING_CARD_TEST_ID,
  type TimelineSlotLookup,
  classifyTimelineInteractionCard,
  missingTimelineCardNode,
} from "@/components/timelineCardSlot";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

afterEach(cleanup);

const emptyLookup: TimelineSlotLookup = {};

describe("classifyTimelineInteractionCard", () => {
  it("checkpoint / plan_review 永远是有意为空", () => {
    expect(
      classifyTimelineInteractionCard(
        "checkpoint",
        { checkpoint_id: "cp-1" },
        emptyLookup,
      ),
    ).toEqual({ kind: "intentionalEmpty" });
    expect(
      classifyTimelineInteractionCard(
        "plan_review",
        { checkpoint_id: "pr-1" },
        emptyLookup,
      ),
    ).toEqual({ kind: "intentionalEmpty" });
  });

  it("escalation 无 slot → missing", () => {
    expect(
      classifyTimelineInteractionCard(
        "escalation",
        { escalation_id: "esc-gone" },
        emptyLookup,
      ),
    ).toEqual({
      kind: "missing",
      processKind: "escalation",
      id: "esc-gone",
    });
  });

  it("approval 无痕迹 → missing；pending 有意为空；resolved → card", () => {
    expect(
      classifyTimelineInteractionCard(
        "approval",
        { approval_id: "ap-gone" },
        emptyLookup,
      ),
    ).toEqual({ kind: "missing", processKind: "approval", id: "ap-gone" });
    expect(
      classifyTimelineInteractionCard(
        "approval",
        { approval_id: "ap-pending" },
        {
          hotTraces: new Map([["ap-pending", { resolved: false }]]),
        },
      ),
    ).toEqual({ kind: "intentionalEmpty" });
    expect(
      classifyTimelineInteractionCard(
        "approval",
        { approval_id: "ap-ok" },
        {
          hotTraces: new Map([["ap-ok", { resolved: true }]]),
        },
      ),
    ).toEqual({ kind: "card" });
  });

  it("stage_card pending 有意为空；未命中 → missing", () => {
    expect(
      classifyTimelineInteractionCard(
        "stage_card",
        { stage_card_id: "sc-pending" },
        {
          stageCardTraces: new Map([["sc-pending", { outcome: "pending" }]]),
        },
      ),
    ).toEqual({ kind: "intentionalEmpty" });
    expect(
      classifyTimelineInteractionCard(
        "stage_card",
        { stage_card_id: "sc-gone" },
        emptyLookup,
      ),
    ).toEqual({
      kind: "missing",
      processKind: "stage_card",
      id: "sc-gone",
    });
  });

  it("team_preview pending 有意为空；未命中 → missing", () => {
    expect(
      classifyTimelineInteractionCard(
        "team_preview",
        { checkpoint_id: "tp-pending" },
        {
          teamPreviewTraces: new Map([["tp-pending", { status: "pending" }]]),
        },
      ),
    ).toEqual({ kind: "intentionalEmpty" });
    expect(
      classifyTimelineInteractionCard(
        "team_preview",
        { checkpoint_id: "tp-gone" },
        emptyLookup,
      ),
    ).toEqual({
      kind: "missing",
      processKind: "team_preview",
      id: "tp-gone",
    });
  });
});

describe("missingTimelineCardNode", () => {
  it("生产分支仍是 null（像素不变）", () => {
    expect(
      missingTimelineCardNode(
        { kind: "missing", processKind: "escalation", id: "esc-1" },
        false,
      ),
    ).toBeNull();
  });
});

describe("ProcessTimeline · 有标记无实体", () => {
  it("escalation 无 slot → missing 占位", () => {
    render(
      <ProcessTimeline
        steps={[{ kind: "escalation", escalation_id: "esc-gone" }]}
        isStreaming={false}
      />,
    );
    expect(
      screen
        .getByTestId(TIMELINE_MISSING_CARD_TEST_ID)
        .getAttribute("data-process-kind"),
    ).toBe("escalation");
  });

  it("approval 无痕迹 → missing 占位", () => {
    render(
      <ProcessTimeline
        steps={[{ kind: "approval", approval_id: "ap-gone" }]}
        isStreaming={false}
      />,
    );
    expect(
      screen
        .getByTestId(TIMELINE_MISSING_CARD_TEST_ID)
        .getAttribute("data-process-kind"),
    ).toBe("approval");
  });

  it("approval pending 仍空白（不是 missing）", () => {
    const { container } = render(
      <ProcessTimeline
        steps={[{ kind: "approval", approval_id: "ap-pending" }]}
        isStreaming={false}
        hotTraces={
          new Map([
            [
              "ap-pending",
              {
                kind: "approval",
                resolved: false,
                denied: false,
              },
            ],
          ])
        }
      />,
    );
    expect(container.textContent).toBe("");
    expect(screen.queryByTestId(TIMELINE_MISSING_CARD_TEST_ID)).toBeNull();
  });

  it("checkpoint / plan_review 有意为空：不出现 missing 占位", () => {
    const { container } = render(
      <ProcessTimeline
        steps={[
          { kind: "checkpoint", checkpoint_id: "cp-1" },
          { kind: "plan_review", checkpoint_id: "pr-1" },
        ]}
        isStreaming={false}
      />,
    );
    expect(screen.queryByTestId(TIMELINE_MISSING_CARD_TEST_ID)).toBeNull();
    expect(container.textContent).toBe("");
  });
});
