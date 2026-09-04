import { teamHasStartedRuns } from "@/components/chat/InlineTeamGraph";
import {
  debateConclusionHook,
  debatePreviewSubtitle,
} from "@/components/chat/debate/debateEntryCopy";
import {
  challengePreviewFromContext,
  debateFacePrimaryFromContext,
  pickAgentNodeIdlePrimary,
} from "@/components/chat/debate/debateFaceCopy";
import { toDebateModel } from "@/components/chat/debate/model";
// @vitest-environment jsdom
/**
 * 辩论 L0 可见性：真实 multi_agent_debate 向量折叠后，辩论室 / 结论钩子 / 图门可消费。
 */
import { planCapabilities } from "@/components/graph/planCapabilities";
import { foldToProjectedTurn } from "@/protocol/conformanceFold";
import type { Execution, RunNode } from "@/stores/execution";
import { loadFixtures } from "@agentcore/protocol-conformance";
import { describe, expect, it } from "vitest";

function toExecution(name: string): Execution {
  const fixture = loadFixtures().find((f) => f.name === name);
  if (!fixture) throw new Error(`missing fixture ${name}`);
  const p = foldToProjectedTurn(fixture.events);
  const runs: RunNode[] = p.runs.map((r) => ({
    id: r.id,
    agentId: r.agentId,
    status: r.status,
    task: r.task,
    dependsOn: r.dependsOn,
    parentRunId: r.parentRunId ?? null,
    kind: r.kind ?? "agent",
    role: r.role ?? null,
    model: r.model ?? "",
    usage: r.usage ?? null,
    cost: r.cost ?? null,
    error: r.error,
    outputSummary: r.outputSummary,
    outputFiles: [],
    debrief: r.debrief,
    durationMs: r.durationMs,
    startedAt: null,
    stance: r.stance ?? null,
    group: r.group ?? null,
    round: r.round ?? 0,
    sideKey: null,
    continuesRunId: r.continuesRunId ?? null,
    continuationIndex: 0,
    replacesRunId: r.replacesRunId ?? null,
    revised: r.revised ?? null,
    checkpoint: r.checkpoint ?? null,
    receivedContext: r.receivedContext ?? [],
    // Desktop RunEscalation 多 id/questions（本机交互态）；golden ProjectedRun 无这两字段。
    escalations: (r.escalations ?? []).map((e) => ({
      id: null,
      question: e.question,
      assumption: e.assumption,
      blocking: e.blocking,
      status: e.status,
      answer: e.answer,
      kind: e.kind ?? "normal",
      questions: [],
      ...(e.awaiting === "ceo" ? { awaiting: "ceo" as const } : {}),
      ...(e.arbitrated_by === "ceo"
        ? {
            arbitrated_by: "ceo" as const,
            ...(e.via_user != null ? { via_user: e.via_user } : {}),
          }
        : {}),
    })),
    process: r.process ?? [],
  }));
  return {
    id: "exec1",
    planType: "debate",
    taskSummary: "正反辩论",
    status: p.status === "running" ? "running" : p.status,
    agents: p.agents.map((a) => ({
      id: a.id,
      role: a.role,
      thinking: a.thinking,
      status: a.status,
      currentRunId: a.currentRunId,
      outputChunks: a.output ? [a.output] : [],
      reasoningChunks: a.reasoning ? [a.reasoning] : [],
      toolCalls: [],
      toolProgress: a.toolProgress,
      toolExecutionLive: null,
    })),
    runs,
    progress: p.progress,
    acts: [],
    batches: [],
    debate: p.debate,
    debateRounds: p.debateRounds,
    crossExamEnabled: p.crossExamEnabled,
    debateOpening: p.debateOpening,
    debatePretrial: null,
  };
}

describe("debate L0 visibility · multi_agent_debate fixture", () => {
  it("debate room + brief CTA + graph gate ready for chat default surface", () => {
    const execution = toExecution("multi_agent_debate");

    expect(planCapabilities(execution.planType).showsTeamGraph).toBe(true);
    expect(teamHasStartedRuns(execution.runs)).toBe(true);

    const model = toDebateModel(execution);
    expect(model).not.toBeNull();
    if (!model) return;
    expect(model.rounds.length).toBeGreaterThan(0);
    expect(model.rounds.some((r) => r.focus || r.summary)).toBe(true);

    expect(debatePreviewSubtitle(execution)).toMatch(/置信/);
    const hook = debateConclusionHook(execution);
    expect(hook?.leaning).toBeTruthy();
  });

  it("continuation run_context drives face primary / challenge over role template", () => {
    const execution = toExecution("multi_agent_debate");
    const cont = execution.runs.find(
      (r) =>
        r.continuesRunId != null &&
        (r.receivedContext?.some((b) => b.channel === "round_focus") ||
          r.receivedContext?.some((b) => b.channel === "task") ||
          r.receivedContext?.some((b) => b.channel === "challenge")),
    );
    expect(cont, "expected a continue_run with context blocks").toBeTruthy();
    if (!cont) return;

    const primary = debateFacePrimaryFromContext(cont.receivedContext);
    const challenge = challengePreviewFromContext(cont.receivedContext);
    const agent = execution.agents.find((a) => a.id === cont.agentId);
    const outputPreview = (agent?.outputChunks ?? []).join("").slice(-80);

    const idle = pickAgentNodeIdlePrimary({
      status: cont.status,
      outputPreview,
      task: cont.task,
      isDebate: true,
      debateFacePrimary: primary,
    });
    expect(idle).toBeTruthy();
    expect(idle).not.toMatch(/你在一场正反辩论中代表/);
    if (challenge) {
      expect(challenge.length).toBeGreaterThan(0);
    }
  });
});
