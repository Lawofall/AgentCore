import {
  shouldShowTeamGraph,
  teamGraphVisible,
  teamHasStartedRuns,
} from "@/components/chat/debatePreviewPlacement";
import { describe, expect, it } from "vitest";

describe("shouldShowTeamGraph", () => {
  it("pending 编制出图（无开工卡闸）", () => {
    expect(
      shouldShowTeamGraph([{ status: "pending" }, { status: "pending" }]),
    ).toBe(true);
  });

  it("零 run 不出图", () => {
    expect(shouldShowTeamGraph([])).toBe(false);
  });

  it("captain 已开 + 工人仍 pending → 出图", () => {
    expect(
      shouldShowTeamGraph([
        { status: "running", kind: "captain" },
        { status: "pending" },
        { status: "pending" },
      ]),
    ).toBe(true);
  });

  it("captain 已开 + 工人仍 pending → 出图（无开工卡闸）", () => {
    expect(
      shouldShowTeamGraph([
        { status: "running", kind: "captain" },
        { status: "pending" },
        { status: "pending" },
      ]),
    ).toBe(true);
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

describe("teamGraphVisible", () => {
  const captainRunning = [
    { status: "running" as const, kind: "captain" as const },
    { status: "pending" as const },
    { status: "pending" as const },
  ];

  it("pending 编制出图（leftover IX 不挡）", () => {
    expect(teamGraphVisible(captainRunning)).toBe(true);
  });

  it("工人已开跑 → 出图", () => {
    expect(
      teamGraphVisible([
        { status: "running", kind: "captain" },
        { status: "running" },
      ]),
    ).toBe(true);
  });

  it("alias shouldShowTeamGraph", () => {
    expect(teamGraphVisible(captainRunning)).toBe(
      shouldShowTeamGraph(captainRunning),
    );
  });
});
