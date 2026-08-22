import {
  isKickoffGoDecision,
  kickoffReleasedFromPreviews,
  shouldHostPreviewInGraph,
  shouldShowTeamGraph,
  teamGraphVisible,
  teamHasStartedRuns,
} from "@/components/chat/debatePreviewPlacement";
import { describe, expect, it } from "vitest";

describe("shouldHostPreviewInGraph", () => {
  const debateResolved = {
    status: "resolved" as const,
    decision: null,
  };
  const delegateResolved = {
    status: "resolved" as const,
    decision: null,
  };
  const go = {
    status: "resolved" as const,
    decision: "continue" as const,
  };
  const stopped = {
    status: "resolved" as const,
    decision: "stop" as const,
  };
  const adjusted = {
    status: "resolved" as const,
    decision: "adjust" as const,
  };
  const pending = {
    status: "pending" as const,
    decision: null,
  };
  const started = [{ status: "running" }];
  const dormant = [{ status: "pending" }, { status: "skipped" }];
  const pendingOnly = [{ status: "pending" }, { status: "pending" }];

  it("debate resolved + team started → hide spare timeline card", () => {
    expect(shouldHostPreviewInGraph(debateResolved, started)).toBe(true);
  });

  it("delegate resolved + team started → hide spare timeline card", () => {
    expect(shouldHostPreviewInGraph(delegateResolved, started)).toBe(true);
  });

  it("resolved without go decision + team not started → keep standalone card", () => {
    expect(shouldHostPreviewInGraph(debateResolved, dormant)).toBe(false);
    expect(shouldHostPreviewInGraph(delegateResolved, dormant)).toBe(false);
  });

  it("resolved continue + pending roster → hide card (graph takes over)", () => {
    expect(shouldHostPreviewInGraph(go, pendingOnly)).toBe(true);
    expect(shouldHostPreviewInGraph(go, dormant)).toBe(true);
  });

  it("resolved stop + never started → keep standalone card", () => {
    expect(shouldHostPreviewInGraph(stopped, pendingOnly)).toBe(false);
    expect(shouldHostPreviewInGraph(stopped, dormant)).toBe(false);
  });

  it("resolved adjust + pending roster → keep standalone card (not go)", () => {
    expect(isKickoffGoDecision("adjust")).toBe(false);
    expect(isKickoffGoDecision("continue")).toBe(true);
    expect(shouldHostPreviewInGraph(adjusted, pendingOnly)).toBe(false);
    expect(shouldHostPreviewInGraph(adjusted, dormant)).toBe(false);
  });

  it("pending → never host (standalone DormantTeamPreview)", () => {
    expect(shouldHostPreviewInGraph(pending, started)).toBe(false);
  });

  it("leftover go + same-bubble pending → do not hide card", () => {
    expect(shouldHostPreviewInGraph(go, pendingOnly, [go, pending])).toBe(
      false,
    );
    expect(shouldHostPreviewInGraph(go, dormant, [go, pending])).toBe(false);
  });

  it("missing preview or runs → false", () => {
    expect(shouldHostPreviewInGraph(null, started)).toBe(false);
    expect(shouldHostPreviewInGraph(debateResolved, null)).toBe(false);
    expect(shouldHostPreviewInGraph(undefined, undefined)).toBe(false);
  });

  it("shares teamGraphVisible with InlineTeamGraph (same gate, no dual-write)", () => {
    expect(shouldShowTeamGraph(started)).toBe(true);
    expect(shouldShowTeamGraph(dormant)).toBe(false);
    expect(shouldShowTeamGraph(pendingOnly, true)).toBe(true);
    expect(shouldShowTeamGraph(pendingOnly, false)).toBe(false);
    expect(shouldHostPreviewInGraph(go, pendingOnly)).toBe(
      teamGraphVisible(pendingOnly, [go]),
    );
    expect(shouldHostPreviewInGraph(stopped, pendingOnly)).toBe(
      teamGraphVisible(pendingOnly, [stopped]),
    );
    expect(shouldHostPreviewInGraph(go, pendingOnly, [go, pending])).toBe(
      teamGraphVisible(pendingOnly, [go, pending]),
    );
    expect(shouldHostPreviewInGraph(debateResolved, dormant)).toBe(
      teamGraphVisible(dormant, [debateResolved]),
    );
  });
});

describe("shouldShowTeamGraph", () => {
  it("开工挂起（未授权）不出图", () => {
    expect(
      shouldShowTeamGraph([{ status: "pending" }, { status: "pending" }]),
    ).toBe(false);
  });

  it("已授权 + pending 编制立刻出图", () => {
    expect(
      shouldShowTeamGraph([{ status: "pending" }, { status: "pending" }], true),
    ).toBe(true);
  });

  it("已授权但还没有节点（零 run）仍不出图", () => {
    expect(shouldShowTeamGraph([], true)).toBe(false);
  });

  it("captain 已开 + 工人仍 pending + 未授权 → 不出图", () => {
    expect(
      shouldShowTeamGraph(
        [
          { status: "running", kind: "captain" },
          { status: "pending" },
          { status: "pending" },
        ],
        false,
      ),
    ).toBe(false);
  });
});

describe("teamHasStartedRuns", () => {
  it("pending / skipped 都不算已开工", () => {
    expect(teamHasStartedRuns([{ status: "pending" }])).toBe(false);
    expect(teamHasStartedRuns([{ status: "skipped" }])).toBe(false);
    expect(teamHasStartedRuns([{ status: "running" }])).toBe(true);
  });

  it("captain running 不算工人已开工", () => {
    expect(
      teamHasStartedRuns([
        { status: "running", kind: "captain" },
        { status: "pending" },
      ]),
    ).toBe(false);
  });
});

describe("kickoffReleasedFromPreviews", () => {
  const go = { status: "resolved" as const, decision: "continue" as const };
  const pending = { status: "pending" as const, decision: null };

  it("无卡 → released（不挡出图）", () => {
    expect(kickoffReleasedFromPreviews([])).toBe(true);
  });

  it("无卡 → released（不挡出图）", () => {
    expect(kickoffReleasedFromPreviews([])).toBe(true);
  });

  it("仅已授权 → released", () => {
    expect(kickoffReleasedFromPreviews([go])).toBe(true);
  });

  it("仅 adjust → 不放行（回灌 CEO，不开工）", () => {
    expect(
      kickoffReleasedFromPreviews([{ status: "resolved", decision: "adjust" }]),
    ).toBe(false);
  });

  it("仅待确认 → 不放行", () => {
    expect(kickoffReleasedFromPreviews([pending])).toBe(false);
  });

  it("上一轮已开做 + 本轮仍 pending → 不放行", () => {
    expect(kickoffReleasedFromPreviews([go, pending])).toBe(false);
  });
});

describe("teamGraphVisible", () => {
  const go = { status: "resolved" as const, decision: "continue" as const };
  const pending = { status: "pending" as const, decision: null };
  const stopped = { status: "resolved" as const, decision: "stop" as const };
  const captainRunning = [
    { status: "running" as const, kind: "captain" as const },
    { status: "pending" as const },
    { status: "pending" as const },
  ];

  it("captain 已开 + 待确认卡 → 画布/详情也不出图", () => {
    expect(teamGraphVisible(captainRunning, [pending])).toBe(false);
  });

  it("开做之后 pending 编制立刻出图", () => {
    expect(teamGraphVisible(captainRunning, [go])).toBe(true);
  });

  it("工人已开跑 → 不依赖开工卡", () => {
    expect(
      teamGraphVisible(
        [{ status: "running", kind: "captain" }, { status: "running" }],
        [pending],
      ),
    ).toBe(true);
  });

  it("取消且卡还在 → 不出图", () => {
    expect(teamGraphVisible(captainRunning, [stopped])).toBe(false);
  });

  it("回放无卡 + pending 工人 → 出图（避免刷新空窗）", () => {
    expect(teamGraphVisible(captainRunning, [])).toBe(true);
  });
});
