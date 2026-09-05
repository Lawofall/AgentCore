import { describe, expect, it } from "vitest";
import {
  FACE_BADGE_BUDGET,
  type FaceBadgeSignals,
  buildFaceBadgeDescriptors,
  pickFaceBadges,
  visibleFaceBadgeKeys,
} from "../faceBudget";

const EMPTY: FaceBadgeSignals = {
  escalationPending: 0,
  escalationRaised: 0,
  escalationKind: null,
  checkpointPending: false,
  checkpointStopped: false,
  checkpointReleased: false,
  revision: false,
  handoff: false,
  crossExamSuffix: false,
};

function signals(extra: Partial<FaceBadgeSignals>): FaceBadgeSignals {
  return { ...EMPTY, ...extra };
}

describe("buildFaceBadgeDescriptors", () => {
  it("maps pending escalation to the decision bucket", () => {
    expect(
      buildFaceBadgeDescriptors(signals({ escalationPending: 2 })),
    ).toEqual([{ key: "escalation", bucket: "decision" }]);
  });

  it("keeps a raised scope/dep escalation as a process notice, drops normal", () => {
    expect(
      buildFaceBadgeDescriptors(
        signals({ escalationRaised: 1, escalationKind: "scope" }),
      ),
    ).toEqual([{ key: "escalation", bucket: "process" }]);
    // normal raised escalation leaves no face badge (matches current render gate).
    expect(
      buildFaceBadgeDescriptors(
        signals({ escalationRaised: 1, escalationKind: "normal" }),
      ),
    ).toEqual([]);
  });

  it("splits checkpoint into decision / anomaly / process", () => {
    expect(
      buildFaceBadgeDescriptors(signals({ checkpointPending: true })),
    ).toEqual([{ key: "checkpoint", bucket: "decision" }]);
    expect(
      buildFaceBadgeDescriptors(signals({ checkpointStopped: true })),
    ).toEqual([{ key: "checkpoint", bucket: "anomaly" }]);
    expect(
      buildFaceBadgeDescriptors(signals({ checkpointReleased: true })),
    ).toEqual([{ key: "checkpoint", bucket: "process" }]);
  });

  it("prefers pending over stopped/released for the shared checkpoint key", () => {
    expect(
      buildFaceBadgeDescriptors(
        signals({
          checkpointPending: true,
          checkpointStopped: true,
          checkpointReleased: true,
        }),
      ),
    ).toEqual([{ key: "checkpoint", bucket: "decision" }]);
    expect(
      buildFaceBadgeDescriptors(
        signals({ checkpointStopped: true, checkpointReleased: true }),
      ),
    ).toEqual([{ key: "checkpoint", bucket: "anomaly" }]);
  });

  it("prefers revision over handoff for the single top-right slot", () => {
    const both = buildFaceBadgeDescriptors(
      signals({ revision: true, handoff: true }),
    );
    expect(both).toEqual([{ key: "revision", bucket: "process" }]);
    expect(buildFaceBadgeDescriptors(signals({ handoff: true }))).toEqual([
      { key: "handoff", bucket: "process" },
    ]);
  });
});

describe("pickFaceBadges", () => {
  it("keeps every badge when at or under budget", () => {
    const picked = visibleFaceBadgeKeys(
      signals({ checkpointStopped: true, crossExamSuffix: true }),
    );
    expect(picked).toEqual(new Set(["checkpoint", "crossExam"]));
  });

  it("caps at 2 and prefers 待拍板 > 异常 > 过程性", () => {
    // decision (escalation) + anomaly (checkpointStopped) + process (revision, crossExam)
    const picked = visibleFaceBadgeKeys(
      signals({
        escalationPending: 1,
        checkpointStopped: true,
        revision: true,
        crossExamSuffix: true,
      }),
    );
    expect(picked).toEqual(new Set(["escalation", "checkpoint"]));
    expect(picked.size).toBe(FACE_BADGE_BUDGET);
    expect(picked.has("revision")).toBe(false);
    expect(picked.has("crossExam")).toBe(false);
  });

  it("keeps two decisions over lower buckets", () => {
    const picked = visibleFaceBadgeKeys(
      signals({
        escalationPending: 1,
        checkpointPending: true,
        revision: true,
      }),
    );
    expect(picked).toEqual(new Set(["escalation", "checkpoint"]));
  });

  it("promotes an anomaly when only one decision is present", () => {
    const picked = visibleFaceBadgeKeys(
      signals({
        checkpointStopped: true,
        revision: true,
        crossExamSuffix: true,
      }),
    );
    // anomaly (checkpoint 已停止) wins the first slot, then first process (revision).
    expect(picked).toEqual(new Set(["checkpoint", "revision"]));
  });

  it("keeps released checkpoint as process and yields extra process to decision/anomaly", () => {
    expect(
      visibleFaceBadgeKeys(
        signals({ checkpointReleased: true, revision: true }),
      ),
    ).toEqual(new Set(["checkpoint", "revision"]));
    expect(
      visibleFaceBadgeKeys(
        signals({
          escalationPending: 1,
          checkpointStopped: true,
          revision: true,
        }),
      ),
    ).toEqual(new Set(["escalation", "checkpoint"]));
  });

  it("respects an explicit budget override", () => {
    const descriptors = buildFaceBadgeDescriptors(
      signals({ escalationPending: 1, checkpointStopped: true }),
    );
    expect(pickFaceBadges(descriptors, 1)).toEqual(new Set(["escalation"]));
    expect(pickFaceBadges(descriptors, 0)).toEqual(new Set());
  });
});
