// @vitest-environment jsdom
/**
 * 聊天默认面推进线：逐轮焦点·小结骨架；无记分。
 */
import type { Execution, RunNode } from "@/stores/execution";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DebateProgressLine } from "../DebateProgressLine";

afterEach(cleanup);

function run(
  partial: Partial<RunNode> & Pick<RunNode, "id" | "agentId">,
): RunNode {
  return {
    status: "running",
    task: "任务",
    dependsOn: [],
    parentRunId: null,
    kind: "agent",
    role: null,
    model: "m",
    usage: null,
    cost: null,
    error: null,
    outputSummary: null,
    outputFiles: [],
    debrief: null,
    durationMs: null,
    startedAt: null,
    stance: null,
    group: null,
    round: 0,
    continuesRunId: null,
    continuationIndex: 0,
    replacesRunId: null,
    revised: null,
    checkpoint: null,
    receivedContext: [],
    escalations: [],
    process: [],
    ...partial,
    sideKey: partial.sideKey ?? null,
  };
}

function baseExecution(overrides: Partial<Execution> = {}): Execution {
  return {
    id: "exec-1",
    planType: "debate",
    taskSummary: "该不该上",
    status: "running",
    agents: [],
    runs: [
      run({ id: "mod", agentId: "a-mod", parentRunId: null }),
      run({
        id: "mod_r1_pro",
        agentId: "a-pro",
        parentRunId: "mod",
        stance: "pro",
        group: "debate:debate",
        round: 1,
      }),
      run({
        id: "mod_r1_con",
        agentId: "a-con",
        parentRunId: "mod",
        stance: "con",
        group: "debate:debate",
        round: 1,
      }),
    ],
    progress: { completed: 0, total: 3 },
    batches: [],
    debate: null,
    debateRounds: [],
    crossExamEnabled: false,
    debateOpening: null,
    debatePretrial: null,
    ...overrides,
    acts: overrides.acts ?? [],
  };
}

describe("DebateProgressLine", () => {
  it("renders expandable focus · summary skeleton (no scores)", () => {
    const execution = baseExecution({
      debateRounds: [
        {
          round_no: 1,
          focus: "成本可控性",
          summary: "双方对预算口径仍有分歧",
          verdict: {
            real_clash: true,
            converged: false,
            rationale: "",
            new_arguments: true,
            stop_reason: "",
          },
          sides: [],
          clashes: [],
          cross_exam: [],
        },
        {
          round_no: 2,
          focus: "上线窗口",
          summary: "",
          verdict: null,
          sides: [],
          clashes: [],
          cross_exam: [],
        },
      ],
    });

    render(
      <DebateProgressLine
        execution={execution}
        disclosureKey="test:debate-progress"
      />,
    );

    expect(screen.getByTestId("debate-progress-line")).toBeTruthy();
    expect(screen.getByText(/推进线 2/)).toBeTruthy();
    expect(screen.getByTestId("debate-progress-round-1").textContent).toContain(
      "焦点 · 成本可控性",
    );
    expect(screen.getByTestId("debate-progress-round-1").textContent).toContain(
      "小结 · 双方对预算口径仍有分歧",
    );
    expect(screen.getByTestId("debate-progress-round-2").textContent).toContain(
      "进行中",
    );
    expect(screen.queryByText(/记分|argument|engagement/i)).toBeNull();

    fireEvent.click(screen.getByLabelText("收起推进线"));
    expect(screen.queryByTestId("debate-progress-round-1")).toBeNull();
  });

  it("returns null for non-debate execution", () => {
    const { container } = render(
      <DebateProgressLine
        execution={baseExecution({
          planType: "multi_agent",
          runs: [run({ id: "w1", agentId: "a1" })],
          debateRounds: [],
        })}
        disclosureKey="test:debate-progress-empty"
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
