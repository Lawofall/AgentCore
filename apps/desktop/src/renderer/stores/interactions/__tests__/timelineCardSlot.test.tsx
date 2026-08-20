// @vitest-environment jsdom
/**
 * 时间线空槽两条路径：有意为空 vs 卡片实体缺失。
 * 以前都返回 null，卡丢失时界面看起来正常，测试也无法断言这一故障。
 */
import {
  ApprovalTrace,
  StageCardTrace,
} from "@/components/chat/HotDecisionTrace";
import { ProcessTimeline } from "@/components/chat/message-bubble/ProcessTimeline";
import type { CheckpointDisplay } from "@/stores/conversation";
import { useInteractionStore } from "@/stores/interactions";
import { renderTimelineInteractionCard } from "@/stores/interactions/registryUi";
import {
  TIMELINE_MISSING_CARD_TEST_ID,
  type TimelineCardBags,
  classifyTimelineInteractionCard,
  missingTimelineCardNode,
} from "@/stores/interactions/timelineCardSlot";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/disclosure", () => ({
  useStreamAwareDisclosure: () => [true, vi.fn()],
  usePersistentDisclosure: () => [false, vi.fn()],
}));

const emptyBags: TimelineCardBags = {
  checkpoints: [],
  planReviews: [],
  teamPreviews: [],
};

const pendingCheckpoint: CheckpointDisplay = {
  id: "cp-1",
  question: "先对齐方向？",
  assumptions: [],
  questions: [],
  intent: "decision",
  status: "pending",
  decision: null,
  note: "",
  selected: [],
};

afterEach(cleanup);

describe("classifyTimelineInteractionCard", () => {
  it("plan_review 永远是有意为空（即使袋子里有卡）", () => {
    const bags: TimelineCardBags = {
      ...emptyBags,
      planReviews: [
        {
          id: "pr-1",
          steps: [],
          pending: [],
          status: "pending",
          decision: null,
          note: "",
        },
      ],
    };
    expect(
      classifyTimelineInteractionCard(
        "plan_review",
        { checkpoint_id: "pr-1" },
        bags,
      ),
    ).toEqual({ kind: "intentionalEmpty" });
  });

  it("时间线有 checkpoint 标记但袋子里没有实体 → missing", () => {
    expect(
      classifyTimelineInteractionCard(
        "checkpoint",
        { checkpoint_id: "cp-gone" },
        emptyBags,
      ),
    ).toEqual({
      kind: "missing",
      processKind: "checkpoint",
      id: "cp-gone",
    });
  });

  it("袋子里有同 id 的 checkpoint（含 pending）→ card，不是 missing", () => {
    expect(
      classifyTimelineInteractionCard(
        "checkpoint",
        { checkpoint_id: "cp-1" },
        { ...emptyBags, checkpoints: [pendingCheckpoint] },
      ),
    ).toEqual({ kind: "card" });
  });

  it("team_preview 袋子未命中是 missing", () => {
    expect(
      classifyTimelineInteractionCard(
        "team_preview",
        { checkpoint_id: "tp-gone" },
        emptyBags,
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
        { kind: "missing", processKind: "checkpoint", id: "cp-1" },
        false,
      ),
    ).toBeNull();
  });
});

describe("renderTimelineInteractionCard", () => {
  it("有标记无实体：开发态能断言出 missing 占位", () => {
    const node = renderTimelineInteractionCard(
      "checkpoint",
      { checkpoint_id: "cp-gone" },
      emptyBags,
    );
    render(node);
    const el = screen.getByTestId(TIMELINE_MISSING_CARD_TEST_ID);
    expect(el.getAttribute("data-process-kind")).toBe("checkpoint");
    expect(el.getAttribute("data-card-id")).toBe("cp-gone");
  });

  it("plan_review 有意为空：不出现 missing 占位", () => {
    const node = renderTimelineInteractionCard(
      "plan_review",
      { checkpoint_id: "pr-1" },
      emptyBags,
    );
    expect(node).toBeNull();
    const { container } = render(node);
    expect(screen.queryByTestId(TIMELINE_MISSING_CARD_TEST_ID)).toBeNull();
    expect(container.textContent).toBe("");
  });

  it("checkpoint 实体在袋子里（pending）不是 missing", () => {
    const node = renderTimelineInteractionCard(
      "checkpoint",
      { checkpoint_id: "cp-1" },
      { ...emptyBags, checkpoints: [pendingCheckpoint] },
    );
    render(node);
    expect(screen.queryByTestId(TIMELINE_MISSING_CARD_TEST_ID)).toBeNull();
  });
});

describe("ProcessTimeline · 有标记无实体", () => {
  it("checkpoint 标记在时间线、袋子为空 → missing 占位", () => {
    render(
      <ProcessTimeline
        process={[{ kind: "checkpoint", checkpoint_id: "cp-gone" }]}
        isStreaming={false}
        citations={[]}
        composingTool={null}
        fallbackContent=""
        conversationId="c1"
        {...emptyBags}
      />,
    );
    const el = screen.getByTestId(TIMELINE_MISSING_CARD_TEST_ID);
    expect(el.getAttribute("data-process-kind")).toBe("checkpoint");
    expect(el.getAttribute("data-card-id")).toBe("cp-gone");
  });

  it("plan_review 标记不画卡、也不报 missing", () => {
    render(
      <ProcessTimeline
        process={[{ kind: "plan_review", checkpoint_id: "pr-1" }]}
        isStreaming={false}
        citations={[]}
        composingTool={null}
        fallbackContent=""
        conversationId="c1"
        {...emptyBags}
      />,
    );
    expect(screen.queryByTestId(TIMELINE_MISSING_CARD_TEST_ID)).toBeNull();
  });
});

describe("热痕迹：pending 有意为空 vs 实体缺失", () => {
  beforeEach(() => {
    useInteractionStore.setState({ byId: new Map() });
  });

  it("ApprovalTrace 查不到 entry → missing 占位", () => {
    render(<ApprovalTrace approvalId="ap-gone" />);
    expect(
      screen
        .getByTestId(TIMELINE_MISSING_CARD_TEST_ID)
        .getAttribute("data-process-kind"),
    ).toBe("approval");
  });

  it("StageCardTrace pending 仍空白（不是 missing）", () => {
    useInteractionStore.getState().upsertRequired({
      kind: "stage_card",
      conversationId: "c1",
      messageId: "m1",
      payload: { stage_card_id: "sc-pending", motion: "命题" },
    });
    const { container } = render(<StageCardTrace stageCardId="sc-pending" />);
    expect(container.textContent).toBe("");
    expect(screen.queryByTestId(TIMELINE_MISSING_CARD_TEST_ID)).toBeNull();
  });

  it("StageCardTrace 查不到 entry → missing 占位", () => {
    render(<StageCardTrace stageCardId="sc-gone" />);
    expect(
      screen
        .getByTestId(TIMELINE_MISSING_CARD_TEST_ID)
        .getAttribute("data-process-kind"),
    ).toBe("stage_card");
  });
});
