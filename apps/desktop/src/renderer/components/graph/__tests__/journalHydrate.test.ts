import type { ExecutionJournal } from "@/stores/execution";
import { useExecutionStore } from "@/stores/execution";
import { beforeEach, describe, expect, it } from "vitest";
import {
  journalHydrateIdentity,
  journalHydrateIdentityEqual,
} from "../journalHydrate";

function journal(
  n: number,
  extra?: Partial<ExecutionJournal>,
): ExecutionJournal {
  return {
    finishReason: "stop",
    events: Array.from({ length: n }, (_, i) => ({
      type: "run_started",
      timestamp: `2026-01-01T00:00:0${i}.000Z`,
      payload: { run_id: `r${i}`, agent_id: "a1" },
    })),
    ...extra,
  };
}

const thinPlan = {
  id: "exec-1",
  planType: "multi_agent" as const,
  taskSummary: "半场",
  agents: [{ id: "agent-1", role: "研究员" }],
  runs: [{ id: "run-1", agentId: "agent-1", task: "调研", dependsOn: [] }],
};

const thickerJournal: ExecutionJournal = {
  finishReason: "stop",
  events: [
    {
      type: "run_plan",
      timestamp: "2026-01-01T00:00:00.000Z",
      payload: {
        execution_id: "exec-1",
        plan_type: "multi_agent",
        task_summary: "半场",
        agents: [
          { id: "agent-1", role: "研究员" },
          { id: "agent-2", role: "写手" },
        ],
        runs: [
          { id: "run-1", agent_id: "agent-1", task: "调研", depends_on: [] },
          { id: "run-2", agent_id: "agent-2", task: "撰写", depends_on: [] },
        ],
      },
    },
    {
      type: "run_started",
      timestamp: "2026-01-01T00:00:01.000Z",
      payload: {
        agent_id: "agent-1",
        run_id: "run-1",
        parent_run_id: null,
        kind: "agent",
      },
    },
    {
      type: "run_started",
      timestamp: "2026-01-01T00:00:02.000Z",
      payload: {
        agent_id: "agent-2",
        run_id: "run-2",
        parent_run_id: null,
        kind: "agent",
      },
    },
  ],
};

describe("journalHydrateIdentity (TurnDetailPage / InlineTeamGraph)", () => {
  it("treats the same runs object + events.length as equal", () => {
    const j = journal(2);
    expect(
      journalHydrateIdentityEqual(
        journalHydrateIdentity(j),
        journalHydrateIdentity(j),
      ),
    ).toBe(true);
  });

  it("treats a later journal object as a new identity", () => {
    const thin = journal(1);
    expect(
      journalHydrateIdentityEqual(
        journalHydrateIdentity(thin),
        journalHydrateIdentity(thickerJournal),
      ),
    ).toBe(false);
  });

  it("treats in-place events.length growth as a new identity", () => {
    const j = journal(1);
    const before = journalHydrateIdentity(j);
    j.events.push({
      type: "run_completed",
      timestamp: "2026-01-01T00:00:09.000Z",
      payload: { run_id: "r0", agent_id: "a1" },
    });
    expect(journalHydrateIdentityEqual(before, journalHydrateIdentity(j))).toBe(
      false,
    );
  });
});

describe("hydrateFromJournal (no !plan gate)", () => {
  beforeEach(() => {
    useExecutionStore.setState({ byId: {} });
  });

  it("hydrates a later journal even when the slot already has a half-court plan", () => {
    const mid = "msg-1";
    useExecutionStore.getState().startExecution(thinPlan, mid);
    expect(useExecutionStore.getState().byId[mid]?.plan?.runs).toHaveLength(1);

    useExecutionStore.getState().hydrateFromJournal(mid, thickerJournal);
    expect(
      useExecutionStore.getState().byId[mid]?.plan?.runs.map((r) => r.id),
    ).toEqual(["run-1", "run-2"]);
  });
});
