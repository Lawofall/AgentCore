import type { AgentState, Execution, RunNode } from "@/stores/execution";
import { describe, expect, it } from "vitest";
import { toDebateModel } from "../projection";

function baseExecution(overrides: Partial<Execution> = {}): Execution {
  return {
    status: "running",
    runs: [],
    agents: [],
    frames: [],
    debate: null,
    debateRounds: [],
    crossExamEnabled: false,
    debateOpening: null,
    ...overrides,
  } as Execution;
}

describe("toDebateModel live cross-exam", () => {
  it("projects in-flight cross-exam from _cx_ runs and run_context before debate_round", () => {
    const execution = baseExecution({
      runs: [
        {
          id: "mod_r1_pro",
          agentId: "mod_r1_pro",
          status: "completed",
          stance: "pro",
          group: "debate:debate",
          round: 1,
          continuationIndex: 0,
          continuesRunId: null,
          parentRunId: null,
          kind: "agent",
          receivedContext: [],
        } as unknown as RunNode,
        {
          id: "mod_r1_con",
          agentId: "mod_r1_con",
          status: "completed",
          stance: "con",
          group: "debate:debate",
          round: 1,
          continuationIndex: 0,
          continuesRunId: null,
          parentRunId: null,
          kind: "agent",
          receivedContext: [],
        } as unknown as RunNode,
        {
          id: "mod_r1_cx_pro",
          agentId: "mod_r1_cx_pro",
          status: "running",
          stance: "pro",
          sideKey: "pro",
          group: "debate:debate",
          round: 1,
          continuationIndex: 1,
          continuesRunId: "mod_r1_pro",
          parentRunId: "mod_r1_pro",
          kind: "agent",
          receivedContext: [
            {
              channel: "cross_exam",
              heading: "第 1 轮 · 质询（必须正面回答）",
              body: "- 收益口径是否含尾部风险？\n- 熔断后成本谁承担？",
              chars: 30,
              truncated: false,
              source_role: "",
              source_run_id: "",
              fidelity: "",
              files: [],
            },
          ],
        } as unknown as RunNode,
      ],
      agents: [
        {
          id: "mod_r1_pro",
          role: "支持方",
          status: "completed",
          outputChunks: ["立论全文"],
          reasoningChunks: [],
          currentRunId: null,
          toolProgress: null,
          toolExecutionLive: null,
        } as unknown as AgentState,
        {
          id: "mod_r1_cx_pro",
          role: "支持方",
          status: "working",
          outputChunks: ["作答：口径未含尾部"],
          reasoningChunks: [],
          currentRunId: "mod_r1_cx_pro",
          toolProgress: null,
          toolExecutionLive: null,
        } as unknown as AgentState,
      ],
      debateRounds: [
        {
          round_no: 1,
          focus: "收益与风险",
          summary: "",
          verdict: null,
          sides: [],
          clashes: [],
          cross_exam: [],
        },
      ],
    });

    const model = toDebateModel(execution);
    expect(model).not.toBeNull();
    if (!model) throw new Error("expected debate model");
    expect(model.settled).toBe(false);
    const round = model.rounds[0];
    expect(round).toBeDefined();
    if (!round) throw new Error("expected round");
    expect(round.crossExam).toHaveLength(1);
    expect(round.crossExam[0].targetKey).toBe("pro");
    expect(round.crossExam[0].exchanges).toHaveLength(2);
    expect(round.crossExam[0].exchanges[0].question).toContain("尾部风险");
    expect(round.crossExam[0].answerRun?.status).toBe("running");
    // 质询 continue_run 继承 stance，不得混入发言格（否则 split 藏格 / stack 重复）。
    expect(round.sides.map((s) => s.key)).toEqual(["mod_r1_pro", "mod_r1_con"]);
  });

  it("prefers structured payload answers over re-parsing answer_run blob", () => {
    const fullText = "流式启发式可能不同的全文";
    const execution = baseExecution({
      runs: [
        {
          id: "mod_r1_cx_pro",
          agentId: "mod_r1_cx_pro",
          status: "completed",
          stance: "pro",
          sideKey: "pro",
          group: "debate:debate",
          round: 1,
          continuationIndex: 1,
          continuesRunId: "mod_r1_pro",
          parentRunId: "mod_r1_pro",
          kind: "agent",
          receivedContext: [
            {
              channel: "cross_exam",
              heading: "质询",
              body: "- Q1?",
              chars: 4,
              truncated: false,
              source_role: "",
              source_run_id: "",
              fidelity: "",
              files: [],
            },
          ],
        } as unknown as RunNode,
      ],
      agents: [
        {
          id: "mod_r1_cx_pro",
          role: "支持方",
          status: "completed",
          outputChunks: [fullText],
          reasoningChunks: [],
          currentRunId: null,
          toolProgress: null,
          toolExecutionLive: null,
        } as unknown as AgentState,
      ],
      debateRounds: [
        {
          round_no: 1,
          focus: "焦点",
          summary: "小结",
          verdict: {
            real_clash: true,
            new_arguments: false,
            converged: true,
            stop_reason: "converged",
            rationale: "r",
          },
          sides: [
            { key: "pro", name: "支持方", run_id: "mod_r1_pro", ok: true },
          ],
          clashes: [],
          cross_exam: [
            {
              target: "pro",
              questioner: "",
              // 新契约：载荷 answer 权威；不再用 run 全文覆盖。
              exchanges: [{ question: "Q1?", answer: "后端解析的权威答案" }],
              answer_run_id: "mod_r1_cx_pro",
            },
          ],
        },
      ],
    });

    const model = toDebateModel(execution);
    expect(model).not.toBeNull();
    const answer = model?.rounds[0]?.crossExam[0]?.exchanges[0]?.answer;
    expect(answer).toBe("后端解析的权威答案");
  });

  it("keeps full answer visible under backend parse-fallback payload + completed run", () => {
    // 后端切不出标题段 → 全文挂 Q1、Q2+ 空；若前端只吃载荷且丢 run，收场会渲成「未作答」。
    const fullBlob =
      "作答：否，口径未含尾部【待核实·推断】。熔断成本由灰度预算池兜底。";
    const execution = baseExecution({
      runs: [
        {
          id: "mod_r1_pro",
          agentId: "mod_r1_pro",
          status: "completed",
          stance: "pro",
          group: "debate:debate",
          round: 1,
          continuationIndex: 0,
          continuesRunId: null,
          parentRunId: null,
          kind: "agent",
          receivedContext: [],
        } as unknown as RunNode,
        {
          id: "mod_r1_cx_pro",
          agentId: "mod_r1_cx_pro",
          status: "completed",
          stance: "pro",
          group: "debate:debate",
          round: 1,
          continuationIndex: 1,
          continuesRunId: "mod_r1_pro",
          parentRunId: "mod_r1_pro",
          kind: "agent",
          receivedContext: [
            {
              channel: "cross_exam",
              heading: "质询",
              body: "- 收益是否计入尾部风险？\n- 熔断成本由谁承担？",
              chars: 40,
              truncated: false,
              source_role: "",
              source_run_id: "",
              fidelity: "",
              files: [],
            },
          ],
        } as unknown as RunNode,
      ],
      agents: [
        {
          id: "mod_r1_pro",
          role: "支持方",
          status: "completed",
          outputChunks: ["立论"],
          reasoningChunks: [],
          currentRunId: null,
          toolProgress: null,
          toolExecutionLive: null,
        } as unknown as AgentState,
        {
          id: "mod_r1_cx_pro",
          role: "支持方",
          status: "completed",
          outputChunks: [fullBlob],
          reasoningChunks: [],
          currentRunId: null,
          toolProgress: null,
          toolExecutionLive: null,
        } as unknown as AgentState,
      ],
      debateRounds: [
        {
          round_no: 1,
          focus: "焦点",
          summary: "小结",
          verdict: {
            real_clash: true,
            new_arguments: false,
            converged: true,
            stop_reason: "converged",
            rationale: "r",
          },
          sides: [
            { key: "pro", name: "支持方", run_id: "mod_r1_pro", ok: true },
          ],
          clashes: [],
          cross_exam: [
            {
              target: "pro",
              questioner: "",
              exchanges: [
                { question: "收益是否计入尾部风险？", answer: "" },
                { question: "熔断成本由谁承担？", answer: "" },
              ],
              answer_run_id: "mod_r1_cx_pro",
            },
          ],
        },
      ],
    });

    const model = toDebateModel(execution);
    expect(model).not.toBeNull();
    const exchanges = model?.rounds[0]?.crossExam[0]?.exchanges ?? [];
    expect(exchanges).toHaveLength(2);
    expect(exchanges[0].answer).toContain("尾部");
    expect(exchanges[0].answer).toBe(fullBlob);
    expect(exchanges[1].answer).toBe("");
    expect(model?.rounds[0]?.sides.map((s) => s.key)).toEqual(["mod_r1_pro"]);
  });
});

describe("toDebateModel live 2-side sideKey (directed follow-up contract, 09 F6)", () => {
  function twoSideExecution(proKey: string, conKey: string): Execution {
    const mkRun = (id: string, stance: "pro" | "con") =>
      ({
        id,
        agentId: id,
        status: "completed",
        stance,
        group: "debate:debate",
        round: 1,
        continuationIndex: 0,
        continuesRunId: null,
        parentRunId: null,
        kind: "agent",
        receivedContext: [],
      }) as unknown as RunNode;
    const mkAgent = (id: string, role: string) =>
      ({
        id,
        role,
        status: "completed",
        outputChunks: [],
        reasoningChunks: [],
        currentRunId: null,
        toolProgress: null,
        toolExecutionLive: null,
      }) as unknown as AgentState;
    return baseExecution({
      runs: [mkRun("mod_r1_pro", "pro"), mkRun("mod_r1_con", "con")],
      agents: [mkAgent("mod_r1_pro", "卖方"), mkAgent("mod_r1_con", "买方")],
      debateRounds: [
        {
          round_no: 1,
          focus: "焦点",
          summary: "",
          verdict: {
            real_clash: true,
            new_arguments: true,
            converged: false,
            stop_reason: "",
            rationale: "",
          },
          sides: [
            { key: proKey, name: "卖方", run_id: "mod_r1_pro", ok: true },
            { key: conKey, name: "买方", run_id: "mod_r1_con", ok: true },
          ],
          clashes: [],
          cross_exam: [],
        },
      ],
    });
  }

  it("uses backend side.key (not stance) so ask_target matches even for non-pro/con keys", () => {
    const model = toDebateModel(twoSideExecution("卖方", "买方"));
    expect(model?.settled).toBe(false);
    const round = model?.rounds[0];
    const pro = round?.sides.find((s) => s.stance === "pro");
    const con = round?.sides.find((s) => s.stance === "con");
    // sideKey（→ 掌舵 ask_target / clash 匹配）取后端真实 key，不再硬编码 stance
    expect(pro?.sideKey).toBe("卖方");
    expect(con?.sideKey).toBe("买方");
    // stance 保留（左右布局 + 固定红蓝对垒色靠它）
    expect(pro?.stance).toBe("pro");
    expect(con?.stance).toBe("con");
  });

  it("stays pro/con when the backend uses the pro/con convention (no-op for common case)", () => {
    const model = toDebateModel(twoSideExecution("pro", "con"));
    const round = model?.rounds[0];
    expect(round?.sides.find((s) => s.stance === "pro")?.sideKey).toBe("pro");
    expect(round?.sides.find((s) => s.stance === "con")?.sideKey).toBe("con");
  });

  it("falls back to stance as sideKey before the round narrative arrives", () => {
    const exec = twoSideExecution("卖方", "买方");
    // 尚无 debate_round 叙事（narr.sides 缺）→ 回退 stance
    const model = toDebateModel(
      baseExecution({ runs: exec.runs, agents: exec.agents }),
    );
    const round = model?.rounds[0];
    expect(round?.sides.find((s) => s.stance === "pro")?.sideKey).toBe("pro");
    expect(round?.sides.find((s) => s.stance === "con")?.sideKey).toBe("con");
  });
});

describe("toDebateModel live opening", () => {
  it("surfaces sticky debateOpening before any debater speaks", () => {
    const model = toDebateModel(
      baseExecution({
        debateOpening: "这场要定的是该不该上方案 A。",
        debateRounds: [
          {
            round_no: 1,
            focus: "收益与风险",
            summary: "",
            verdict: null,
            sides: [],
            clashes: [],
            cross_exam: [],
          },
        ],
        runs: [
          {
            id: "mod_r1_pro",
            agentId: "mod_r1_pro",
            status: "pending",
            stance: "pro",
            group: "debate:debate",
            round: 1,
            continuationIndex: 0,
            continuesRunId: null,
            parentRunId: null,
            kind: "agent",
            receivedContext: [],
          } as unknown as RunNode,
          {
            id: "mod_r1_con",
            agentId: "mod_r1_con",
            status: "pending",
            stance: "con",
            group: "debate:debate",
            round: 1,
            continuationIndex: 0,
            continuesRunId: null,
            parentRunId: null,
            kind: "agent",
            receivedContext: [],
          } as unknown as RunNode,
        ],
        agents: [
          {
            id: "mod_r1_pro",
            role: "支持方",
            status: "idle",
            outputChunks: [],
            reasoningChunks: [],
          } as unknown as AgentState,
          {
            id: "mod_r1_con",
            role: "反对方",
            status: "idle",
            outputChunks: [],
            reasoningChunks: [],
          } as unknown as AgentState,
        ],
      }),
    );
    expect(model?.settled).toBe(false);
    expect(model?.opening).toBe("这场要定的是该不该上方案 A。");
  });

  it("prefers settled debate.opening over live sticky when收场", () => {
    const model = toDebateModel(
      baseExecution({
        status: "completed",
        debateOpening: "live sticky 不应再露面",
        debate: {
          form: "debate",
          motion: "是否采用方案 A",
          stop_reason: "converged",
          opening: "收场权威开场白",
          narrative_first: false,
          moderator_run_id: "mod",
          execution_id: "exec1",
          sides: [
            {
              key: "pro",
              name: "支持方",
              stance: "支持",
              is_subject: false,
            },
            {
              key: "con",
              name: "反对方",
              stance: "反对",
              is_subject: false,
            },
          ],
          rounds: [
            {
              round_no: 1,
              focus: "焦点",
              summary: "小结",
              verdict: {
                real_clash: true,
                new_arguments: false,
                converged: true,
                stop_reason: "converged",
                rationale: "够了",
              },
              sides: [
                { key: "pro", name: "支持方", run_id: "mod_r1_pro", ok: true },
                { key: "con", name: "反对方", run_id: "mod_r1_con", ok: true },
              ],
              clashes: [],
              cross_exam: [],
              scores: {},
              user_interjections: [],
            },
          ],
          closings: [],
          brief: {
            crux: "c",
            strongest_points: {},
            handoffs: [],
            decisive: "",
            leaning: "l",
            confidence: "medium",
            recommendation: "r",
          },
        },
        runs: [
          {
            id: "mod_r1_pro",
            agentId: "mod_r1_pro",
            status: "completed",
            stance: "pro",
            group: "debate:debate",
            round: 1,
            continuationIndex: 0,
            continuesRunId: null,
            parentRunId: null,
            kind: "agent",
            receivedContext: [],
          } as unknown as RunNode,
          {
            id: "mod_r1_con",
            agentId: "mod_r1_con",
            status: "completed",
            stance: "con",
            group: "debate:debate",
            round: 1,
            continuationIndex: 0,
            continuesRunId: null,
            parentRunId: null,
            kind: "agent",
            receivedContext: [],
          } as unknown as RunNode,
        ],
        agents: [
          {
            id: "mod_r1_pro",
            role: "支持方",
            status: "completed",
            outputChunks: ["正方"],
            reasoningChunks: [],
          } as unknown as AgentState,
          {
            id: "mod_r1_con",
            role: "反对方",
            status: "completed",
            outputChunks: ["反方"],
            reasoningChunks: [],
          } as unknown as AgentState,
        ],
      }),
    );
    expect(model?.settled).toBe(true);
    expect(model?.opening).toBe("收场权威开场白");
  });
});

describe("toDebateModel live empty shell (no pretrial UI)", () => {
  it("returns empty-rounds skeleton when debatePretrial fold exists", () => {
    const model = toDebateModel(
      baseExecution({
        planType: "debate",
        debatePretrial: {
          status: "running",
          thorough: true,
          skipReason: null,
          sides: [{ key: "pro", name: "正方" }],
          orders: [],
          evidenceLedgerCount: 0,
          fallbackSelfSearch: false,
          evidenceReady: false,
        },
      }),
    );
    expect(model).not.toBeNull();
    expect(model?.settled).toBe(false);
    expect(model?.rounds).toEqual([]);
    expect(model?.form).toBe("debate");
  });

  it("returns empty-rounds skeleton from opening alone", () => {
    const model = toDebateModel(
      baseExecution({
        debateOpening: "先从最要害切入。",
      }),
    );
    expect(model).not.toBeNull();
    expect(model?.rounds).toEqual([]);
    expect(model?.opening).toBe("先从最要害切入。");
  });

  it("returns null when no rounds and no debate shell signal", () => {
    expect(toDebateModel(baseExecution())).toBeNull();
  });
});
