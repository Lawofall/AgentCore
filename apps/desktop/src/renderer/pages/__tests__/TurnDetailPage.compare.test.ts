import { type Execution, hasContinuations, isDebate } from "@/stores/execution";
import { describe, expect, it } from "vitest";
import { isDebateViewPending, resolveTurnDetailView } from "../turnDetailView";

/**
 * Mirrors TurnDetailPage showCompare gate: revisable runs only — never
 * `revisable && !isDebate` (that hid 对比 on mixed multi_agent+debate graphs).
 */
function showCompare(execution: Execution | null | undefined): boolean {
  return !!execution && hasContinuations(execution);
}

describe("TurnDetailPage showCompare gate", () => {
  it("shows compare when revisable even if the graph is a debate", () => {
    const withDebateAndRev = {
      planType: "debate",
      debate: { form: "debate" },
      runs: [
        {
          id: "r1",
          continuesRunId: null,
          stance: "pro",
          group: "debate:main",
        },
        {
          id: "r1b",
          continuesRunId: "r1",
          stance: "pro",
          group: "debate:main",
        },
      ],
    } as unknown as Execution;
    expect(isDebate(withDebateAndRev)).toBe(true);
    expect(hasContinuations(withDebateAndRev)).toBe(true);
    expect(showCompare(withDebateAndRev)).toBe(true);
    // Old buggy gate: revisable && !debate → false
    expect(
      hasContinuations(withDebateAndRev) && !isDebate(withDebateAndRev),
    ).toBe(false);
  });

  it("hides compare when there are no revisable runs", () => {
    const plain = {
      planType: "multi_agent",
      runs: [{ id: "r1", continuesRunId: null }],
    } as unknown as Execution;
    expect(showCompare(plain)).toBe(false);
  });
});

describe("resolveTurnDetailView (双入口契约 B)", () => {
  const debateExec = {
    planType: "debate",
    acts: [{ actId: "act-1", kind: "debate" }],
    debate: { form: "debate" },
    runs: [{ id: "r1", stance: "pro", group: "debate:debate" }],
  } as unknown as Execution;

  const multiExec = {
    planType: "multi_agent",
    acts: [{ actId: "act-1", kind: "multi_agent" }],
    runs: [{ id: "r1", continuesRunId: null }],
  } as unknown as Execution;

  it("honors view=debate when debate signal is present", () => {
    expect(
      resolveTurnDetailView({
        requestedView: "debate",
        debate: true,
        showCompare: false,
        execution: debateExec,
      }),
    ).toBe("debate");
  });

  it("falls back when view=debate but turn is not a debate", () => {
    expect(
      resolveTurnDetailView({
        requestedView: "debate",
        debate: false,
        showCompare: false,
        execution: multiExec,
      }),
    ).toBe("graph");
  });

  it("defaults pure debate to debate room without view param", () => {
    expect(
      resolveTurnDetailView({
        requestedView: null,
        debate: true,
        showCompare: false,
        execution: debateExec,
      }),
    ).toBe("debate");
  });
});

describe("isDebateViewPending (防闪图)", () => {
  it("holds while hydrate is loading for view=debate", () => {
    expect(
      isDebateViewPending({
        requestedView: "debate",
        debate: false,
        hydratePhase: "loading",
        hasJournalToProject: false,
        hasExecution: false,
      }),
    ).toBe(true);
  });

  it("holds after ready while journal exists but execution not projected", () => {
    expect(
      isDebateViewPending({
        requestedView: "debate",
        debate: false,
        hydratePhase: "ready",
        hasJournalToProject: true,
        hasExecution: false,
      }),
    ).toBe(true);
  });

  it("releases once debate is known", () => {
    expect(
      isDebateViewPending({
        requestedView: "debate",
        debate: true,
        hydratePhase: "ready",
        hasJournalToProject: true,
        hasExecution: true,
      }),
    ).toBe(false);
  });

  it("releases when ready with no journal (fallback path)", () => {
    expect(
      isDebateViewPending({
        requestedView: "debate",
        debate: false,
        hydratePhase: "ready",
        hasJournalToProject: false,
        hasExecution: false,
      }),
    ).toBe(false);
  });

  it("does not hold for non-debate view requests", () => {
    expect(
      isDebateViewPending({
        requestedView: "graph",
        debate: false,
        hydratePhase: "loading",
        hasJournalToProject: true,
        hasExecution: false,
      }),
    ).toBe(false);
  });
});
