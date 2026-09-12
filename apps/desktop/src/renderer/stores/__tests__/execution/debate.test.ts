import type { DebateResultPayload } from "@/types/events";
import { beforeEach, describe, expect, it } from "vitest";
import {
  type ExecutionPlan,
  type RunFrame,
  continuationChains,
  debateGroups,
  debateLiveRounds,
  debateSides,
  execRuntime,
  hasContinuations,
  isDebate,
  planFromRunPlan,
  projectExecution,
} from "../../execution";
import {
  MID,
  plan,
  resetExecutionStore,
  revised,
  started,
  store,
} from "./fixtures";

beforeEach(() => {
  resetExecutionStore();
});

describe("辩论/审查 display tags (前端UX设计.md §四)", () => {
  const debatePlan: ExecutionPlan = {
    id: "exec-d",
    planType: "multi_agent",
    taskSummary: "该不该上微服务",
    agents: [
      { id: "a-pro", role: "正方" },
      { id: "a-con", role: "反方" },
    ],
    runs: [
      {
        id: "r-pro",
        agentId: "a-pro",
        task: "支持",
        dependsOn: [],
        stance: "pro",
        group: "g",
      },
      {
        id: "r-con",
        agentId: "a-con",
        task: "反对",
        dependsOn: [],
        stance: "con",
        group: "g",
      },
    ],
  };

  const debateResult: DebateResultPayload = {
    execution_id: "exec-d",
    moderator_run_id: "mod-1",
    form: "debate",
    motion: "该不该上微服务",
    stop_reason: "converged",
    narrative_first: false,
    sides: [
      { key: "pro", name: "正方", stance: "支持上微服务", is_subject: false },
      { key: "con", name: "反方", stance: "反对上微服务", is_subject: false },
    ],
    rounds: [
      {
        round_no: 1,
        focus: "拆分边界与运维成本",
        summary: "正方强调可独立扩展，反方指出运维负担重，焦点收敛到团队规模。",
        verdict: {
          real_clash: true,
          new_arguments: false,
          converged: true,
          stop_reason: "分歧已充分暴露，无新论据",
          rationale: "争点收敛到团队规模这一关键点",
        },
        sides: [
          { key: "pro", name: "正方", run_id: "r-pro", ok: true },
          { key: "con", name: "反方", run_id: "r-con", ok: true },
        ],
        clashes: [
          {
            from_key: "con",
            to_key: "pro",
            point: "可独立扩展的前提是团队规模够大，当前规模撑不起运维。",
          },
        ],
      },
    ],
    brief: {
      crux: "团队规模是否撑得起微服务运维",
      strongest_points: { pro: "可独立扩展", con: "运维成本高" },
      handoffs: [
        { kind: "value", text: "迭代速度优先 vs 稳定优先" },
        { kind: "fact", text: "当前 QPS 峰值口径不一致" },
        { kind: "question", text: "触发拆分的指标阈值如何设定？" },
      ],
      leaning: "倾向暂缓",
      confidence: "medium",
      recommendation: "先做模块化单体，规模上来再按域拆分。",
    },
  };

  it("projectExecution carries stance/group onto the run nodes", () => {
    const exec = projectExecution(debatePlan, [], "running");
    expect(exec.runs.find((r) => r.id === "r-pro")).toMatchObject({
      stance: "pro",
      group: "g",
    });
    expect(exec.runs.find((r) => r.id === "r-con")).toMatchObject({
      stance: "con",
      group: "g",
    });
  });

  it("ordinary runs default to null tags (守住「形状是数据不是模式」)", () => {
    // The普通并行 plan declares no stance — a debate is the only thing that tags
    // runs, so an untagged turn must project null (not a stray side).
    const exec = projectExecution(plan, [], "running");
    expect(exec.runs.every((r) => r.stance === null && r.group === null)).toBe(
      true,
    );
  });

  it("isDebate is true when runs carry a stance OR debate products exist", () => {
    expect(isDebate(projectExecution(debatePlan, [], "running"))).toBe(true);
    expect(isDebate(projectExecution(plan, [], "running"))).toBe(false);
    // 收场产物是辩论的强信号——即便 runs 不带 stance（旧标签缺失 / roundtable 多方）。
    const withProducts = projectExecution(plan, [], "completed", debateResult);
    expect(isDebate(withProducts)).toBe(true);
  });

  it("projectExecution carries the debate products (简报 + 叙事线) verbatim", () => {
    const exec = projectExecution(debatePlan, [], "completed", debateResult);
    expect(exec.debate).toBe(debateResult);
    // 普通团队回合不背辩论字段（默认 null）。
    expect(projectExecution(plan, [], "running").debate).toBeNull();
  });

  it("recordDebateResult stores the live debate products on the slot", () => {
    // Live 路径：streamConversation 收到 debate_result → recordDebateResult 写入 slot，
    // 之后 useMessageExecution 经 projectExecution 把它带给辩论视图。
    store().startExecution(debatePlan, MID);
    store().recordDebateResult(debateResult, MID);
    expect(execRuntime(store(), MID).debate).toBe(debateResult);
  });

  it("debateSides splits the roster by side in plan order", () => {
    const sides = debateSides(projectExecution(debatePlan, [], "running"));
    expect(sides.pro.map((r) => r.id)).toEqual(["r-pro"]);
    expect(sides.con.map((r) => r.id)).toEqual(["r-con"]);
  });

  it("debateGroups buckets opposing runs by group tag (multi-dimension review)", () => {
    const multi: ExecutionPlan = {
      id: "exec-m",
      planType: "multi_agent",
      taskSummary: "多维审查",
      agents: [
        { id: "a1", role: "架构正" },
        { id: "a2", role: "架构反" },
        { id: "a3", role: "选型正" },
      ],
      runs: [
        {
          id: "r1",
          agentId: "a1",
          task: "t",
          dependsOn: [],
          stance: "pro",
          group: "架构",
        },
        {
          id: "r2",
          agentId: "a2",
          task: "t",
          dependsOn: [],
          stance: "con",
          group: "架构",
        },
        // An asymmetric second group (only one side) still forms its own row.
        {
          id: "r3",
          agentId: "a3",
          task: "t",
          dependsOn: [],
          stance: "pro",
          group: "选型",
        },
      ],
    };
    const groups = debateGroups(projectExecution(multi, [], "running"));
    expect(groups.map((g) => g.key)).toEqual(["架构", "选型"]);
    expect(groups[0].pro.map((r) => r.id)).toEqual(["r1"]);
    expect(groups[0].con.map((r) => r.id)).toEqual(["r2"]);
    expect(groups[1].pro.map((r) => r.id)).toEqual(["r3"]);
    expect(groups[1].con).toEqual([]);
  });

  it("debateGroups collapses untagged stances into one default group", () => {
    const noGroup: ExecutionPlan = {
      ...debatePlan,
      runs: debatePlan.runs.map(({ group: _g, ...r }) => r),
    };
    const groups = debateGroups(projectExecution(noGroup, [], "running"));
    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe("");
    expect(groups[0].pro).toHaveLength(1);
    expect(groups[0].con).toHaveLength(1);
  });

  it("debateLiveRounds reconstructs 圆桌/红队 multi-side rounds from revision chains", () => {
    // 多方（无 stance）：首轮是 plan 节点（group debate:*），后续轮是续写 revision
    // (revision N == 第 N 轮)，故 debateGroups 看不到、需走 revision 链重建逐轮。
    const roundtablePlan: ExecutionPlan = {
      id: "exec-rt",
      planType: "multi_agent",
      taskSummary: "圆桌",
      agents: [
        { id: "a1", role: "中央视角" },
        { id: "a2", role: "去中心视角" },
        { id: "a3", role: "混合视角" },
      ],
      runs: [
        {
          id: "d_r1_1",
          agentId: "a1",
          task: "t",
          dependsOn: [],
          group: "debate:roundtable",
          round: 1,
        },
        {
          id: "d_r1_2",
          agentId: "a2",
          task: "t",
          dependsOn: [],
          group: "debate:roundtable",
          round: 1,
        },
        {
          id: "d_r1_3",
          agentId: "a3",
          task: "t",
          dependsOn: [],
          group: "debate:roundtable",
          round: 1,
        },
      ],
    };

    // 仅首轮：无 stance ⇒ debateGroups 空；debateLiveRounds 给出第 1 轮的三方。
    const r1 = projectExecution(roundtablePlan, [], "running");
    expect(debateGroups(r1)).toHaveLength(0);
    const live1 = debateLiveRounds(r1);
    expect(live1).toHaveLength(1);
    expect(live1[0].round).toBe(1);
    expect(live1[0].runs.map((r) => r.id)).toEqual([
      "d_r1_1",
      "d_r1_2",
      "d_r1_3",
    ]);

    // 第 2 轮续写：两方已续、一方未续 ⇒ 第 2 轮只含续到的两方（诚实留空，不假装）。
    const r2 = projectExecution(
      roundtablePlan,
      [
        revised("d_r2_1", "d_r1_1", 2, 1, 2),
        revised("d_r2_2", "d_r1_2", 2, 1, 2),
      ],
      "running",
    );
    const live2 = debateLiveRounds(r2);
    expect(live2.map((r) => r.round)).toEqual([1, 2]);
    expect(live2[1].runs.map((r) => r.id)).toEqual(["d_r2_1", "d_r2_2"]);
  });

  it("planFromRunPlan maps the wire stance/group through to the plan", () => {
    const wirePlan = planFromRunPlan({
      execution_id: "exec-d",
      plan_type: "multi_agent",
      task_summary: "t",
      agents: [
        {
          id: "a-pro",
          role: "正方",
          thinking: true,
        },
      ],
      runs: [
        {
          id: "r-pro",
          agent_id: "a-pro",
          task: "支持",
          depends_on: [],
          stance: "pro",
          group: "g",
        },
      ],
    });
    expect(wirePlan.runs[0]).toMatchObject({ stance: "pro", group: "g" });
  });

  it("projectExecution defaults round to 0 and carries an explicit round", () => {
    // round is display-only (真·多轮辩论): absent ⇒ 0 (single-round), present ⇒ the
    // 1-based turn, projected onto the node the immutable way stance/group are.
    const exec = projectExecution(debatePlan, [], "running");
    expect(exec.runs.every((r) => r.round === 0)).toBe(true);

    const roundedPlan: ExecutionPlan = {
      ...debatePlan,
      runs: debatePlan.runs.map((r, i) => ({ ...r, round: i + 1 })),
    };
    const rounded = projectExecution(roundedPlan, [], "running");
    expect(rounded.runs.find((r) => r.id === "r-pro")?.round).toBe(1);
    expect(rounded.runs.find((r) => r.id === "r-con")?.round).toBe(2);
  });

  it("debateGroups buckets a group's runs by round (真·多轮辩论, 升序)", () => {
    // Two rounds of pro/con in one group: cross-round depends_on wires the exchange,
    // round tags let the card lay it out 逐轮. Buckets come back round-ascending,
    // while the flat pro/con rosters stay whole for the single-round layout.
    const debate3: ExecutionPlan = {
      id: "exec-3",
      planType: "multi_agent",
      taskSummary: "多轮辩论",
      agents: [
        { id: "a-pro", role: "正方" },
        { id: "a-con", role: "反方" },
      ],
      runs: [
        {
          id: "p1",
          agentId: "a-pro",
          task: "t",
          dependsOn: [],
          stance: "pro",
          group: "g",
          round: 1,
        },
        {
          id: "c1",
          agentId: "a-con",
          task: "t",
          dependsOn: [],
          stance: "con",
          group: "g",
          round: 1,
        },
        {
          id: "p2",
          agentId: "a-pro",
          task: "t",
          dependsOn: ["c1"],
          stance: "pro",
          group: "g",
          round: 2,
        },
        {
          id: "c2",
          agentId: "a-con",
          task: "t",
          dependsOn: ["p1"],
          stance: "con",
          group: "g",
          round: 2,
        },
      ],
    };
    const groups = debateGroups(projectExecution(debate3, [], "running"));
    expect(groups).toHaveLength(1);
    expect(groups[0].rounds.map((r) => r.round)).toEqual([1, 2]);
    expect(groups[0].rounds[0].pro.map((r) => r.id)).toEqual(["p1"]);
    expect(groups[0].rounds[0].con.map((r) => r.id)).toEqual(["c1"]);
    expect(groups[0].rounds[1].pro.map((r) => r.id)).toEqual(["p2"]);
    expect(groups[0].rounds[1].con.map((r) => r.id)).toEqual(["c2"]);
    expect(groups[0].pro.map((r) => r.id)).toEqual(["p1", "p2"]);
  });

  it("debateGroups yields one round-0 bucket for a single-round debate", () => {
    // No round tags ⇒ a lone round-0 bucket, so the card keeps the flat 正/反 grid.
    const groups = debateGroups(projectExecution(debatePlan, [], "running"));
    expect(groups[0].rounds.map((r) => r.round)).toEqual([0]);
    expect(groups[0].rounds.some((r) => r.round > 0)).toBe(false);
  });

  it("debateGroups excludes cross_exam/closing continue_runs from speech buckets", () => {
    // 质询/结辩 continue_run 继承原辩手 stance+round，若只按 stance 分桶会混入发言格。
    const base = projectExecution(debatePlan, [], "running");
    const pro = base.runs.find((r) => r.id === "r-pro");
    if (!pro) throw new Error("expected r-pro");
    const block = (
      channel: "cross_exam" | "closing",
    ): (typeof pro.receivedContext)[number] => ({
      channel,
      heading: channel,
      body: "",
      chars: 0,
      truncated: false,
      source_role: "",
      source_run_id: "",
      fidelity: "",
      files: [],
    });
    base.runs.push(
      {
        ...pro,
        id: "r-pro-cx",
        continuesRunId: "r-pro",
        continuationIndex: 1,
        receivedContext: [block("cross_exam")],
      },
      {
        ...pro,
        id: "r-pro-closing",
        continuesRunId: "r-pro",
        continuationIndex: 2,
        receivedContext: [block("closing")],
      },
    );
    const groups = debateGroups(base);
    expect(groups[0].pro.map((r) => r.id)).toEqual(["r-pro"]);
    expect(groups[0].rounds[0].pro.map((r) => r.id)).toEqual(["r-pro"]);
  });

  it("debateLiveRounds excludes cross_exam continue_runs from multi-side speech rows", () => {
    const roundtablePlan: ExecutionPlan = {
      id: "exec-rt-cx",
      planType: "multi_agent",
      taskSummary: "圆桌质询混桶",
      agents: [{ id: "a1", role: "中央" }],
      runs: [
        {
          id: "d_r1_1",
          agentId: "a1",
          task: "t",
          dependsOn: [],
          group: "debate:roundtable",
          round: 1,
        },
      ],
    };
    const base = projectExecution(
      roundtablePlan,
      [revised("d_r1_1_cx", "d_r1_1", 2, 1, 1)],
      "running",
    );
    const cx = base.runs.find((r) => r.id === "d_r1_1_cx");
    if (!cx) throw new Error("expected cx run");
    cx.receivedContext = [
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
    ];
    const live = debateLiveRounds(base);
    expect(live).toHaveLength(1);
    expect(live[0].runs.map((r) => r.id)).toEqual(["d_r1_1"]);
  });

  it("planFromRunPlan maps the wire round through to the plan", () => {
    const wirePlan = planFromRunPlan({
      execution_id: "exec-r",
      plan_type: "multi_agent",
      task_summary: "t",
      agents: [
        {
          id: "a-pro",
          role: "正方",
          thinking: true,
        },
      ],
      runs: [
        {
          id: "r-pro",
          agent_id: "a-pro",
          task: "支持",
          depends_on: [],
          stance: "pro",
          group: "g",
          round: 2,
        },
      ],
    });
    expect(wirePlan.runs[0]).toMatchObject({
      stance: "pro",
      group: "g",
      round: 2,
    });
  });
});

describe("定向唤回 版本链 (乙 热修 P4)", () => {
  function completed(runId: string, agentId: string, t: number): RunFrame {
    return {
      t,
      kind: "run_completed",
      runId,
      agentId,
      outputSummary: "done",
      durationMs: 1,
    };
  }

  it("ordinary runs default to no continuation (continuesRunId null, index 0)", () => {
    // 接续 is the only thing that marks a run; a plain plan must
    // project null/0 (not a stray version).
    const exec = projectExecution(plan, [], "running");
    expect(
      exec.runs.every(
        (r) => r.continuesRunId === null && r.continuationIndex === 0,
      ),
    ).toBe(true);
    expect(hasContinuations(exec)).toBe(false);
    expect(continuationChains(exec)).toEqual([]);
  });

  it("synthesizes a 接续 node + agent from a continues_run_id run_started (not in plan)", () => {
    // A continuation is born from its frame, NOT the plan — so without synthesis it
    // would be dropped. It must materialize, hang off the original, and fold its
    // own output through the inherited (original) display identity.
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      completed("run-1", "agent-1", 2),
      revised("run-1_rev1", "run-1", 2, 3),
      {
        t: 4,
        kind: "run_output_delta",
        runId: "run-1_rev1",
        agentId: "run-1_rev1",
        delta: "改后",
      },
      {
        t: 5,
        kind: "run_output_delta",
        runId: "run-1_rev1",
        agentId: "run-1_rev1",
        delta: "内容",
      },
      completed("run-1_rev1", "run-1_rev1", 6),
    ];
    const exec = projectExecution(plan, frames, "completed");

    const rev = exec.runs.find((r) => r.id === "run-1_rev1");
    expect(rev).toBeTruthy();
    expect(rev?.continuesRunId).toBe("run-1");
    expect(rev?.continuationIndex).toBe(1);
    expect(rev?.status).toBe("completed");
    // inherits the original worker's display role (not the raw run id)
    const revAgent = exec.agents.find((a) => a.id === "run-1_rev1");
    expect(revAgent?.role).toBe("React 研究员");
    expect(revAgent?.outputChunks.join("")).toBe("改后内容");
    // the original keeps its own output (the version chain preserves每版)
    expect(exec.runs.find((r) => r.id === "run-1")?.status).toBe("completed");
  });

  it("ignores a revision whose original is not on the graph (no mis-draw)", () => {
    const frames: RunFrame[] = [revised("ghost_rev1", "ghost", 2)];
    const exec = projectExecution(plan, frames, "running");
    expect(exec.runs.find((r) => r.id === "ghost_rev1")).toBeUndefined();
    expect(exec.runs).toHaveLength(3); // only the plan's own runs
  });

  it("continuationChains builds 现场根 + 续写 in event/continuationIndex order", () => {
    // Wire 已无 revision 序号；链序由事件序（continuationIndex）保证。
    // 先到的续写是 续×1，后到的是 续×2。
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      completed("run-1", "agent-1", 2),
      revised("run-1_rev_a", "run-1", 2, 3),
      completed("run-1_rev_a", "run-1_rev_a", 4),
      revised("run-1_rev_b", "run-1", 3, 5),
      completed("run-1_rev_b", "run-1_rev_b", 6),
    ];
    const exec = projectExecution(plan, frames, "completed");

    expect(hasContinuations(exec)).toBe(true);
    const chains = continuationChains(exec);
    expect(chains).toHaveLength(1);
    expect(chains[0].originalId).toBe("run-1");
    expect(chains[0].versions.map((v) => v.version)).toEqual([1, 2, 3]);
    expect(chains[0].versions.map((v) => v.run.id)).toEqual([
      "run-1",
      "run-1_rev_a",
      "run-1_rev_b",
    ]);
  });

  it("continuationChains yields one chain per revised worker, in graph order", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      completed("run-1", "agent-1", 2),
      started("agent-2", "run-2"),
      completed("run-2", "agent-2", 3),
      // revise run-2 first, then run-1 — chains still follow graph (run) order.
      revised("run-2_rev1", "run-2", 2, 4),
      completed("run-2_rev1", "run-2_rev1", 5),
      revised("run-1_rev1", "run-1", 2, 6),
      completed("run-1_rev1", "run-1_rev1", 7),
    ];
    const exec = projectExecution(plan, frames, "completed");
    const chains = continuationChains(exec);
    expect(chains.map((c) => c.originalId)).toEqual(["run-1", "run-2"]);
  });

  it("is a pure prefix fold — a revision appears only past its run_started", () => {
    const frames: RunFrame[] = [
      started("agent-1", "run-1"),
      completed("run-1", "agent-1", 2),
      revised("run-1_rev1", "run-1", 2, 3),
      completed("run-1_rev1", "run-1_rev1", 4),
    ];
    // playhead before the revision frame → no revision node yet.
    const before = projectExecution(plan, frames.slice(0, 2), "running");
    expect(hasContinuations(before)).toBe(false);
    // full stream → the revision is present.
    const after = projectExecution(plan, frames, "completed");
    expect(hasContinuations(after)).toBe(true);
  });
});

// 乙 wire 携 round/stance (单一轮次投影): a debate 续写 (辩手后续轮) carries its debater
// identity (stance/group) + its TRUE round on the run_started, so the fold projects them onto
// the synthesized 修订 node. That root-fixes the live 2方 view dropping rounds≥2 (debateGroups
// only buckets stance-tagged runs) and gives every view ONE `round` field to read. A legacy
// revision frame (no wire fields) falls back to inheriting the original's stance/group +
// revision-as-round. Mirrors the backend oracle + mobile fold (conformance pins them equal).
describe("乙 wire 携 round/stance (单一轮次投影)", () => {
  const debatePlan2: ExecutionPlan = {
    id: "exec-w",
    planType: "multi_agent",
    taskSummary: "正反辩论",
    agents: [
      { id: "a-pro", role: "支持方" },
      { id: "a-con", role: "反对方" },
    ],
    runs: [
      {
        id: "pro1",
        agentId: "a-pro",
        task: "支持",
        dependsOn: [],
        stance: "pro",
        group: "debate:debate",
        round: 1,
      },
      {
        id: "con1",
        agentId: "a-con",
        task: "反对",
        dependsOn: [],
        stance: "con",
        group: "debate:debate",
        round: 1,
      },
    ],
  };

  // A round-N debater 续写 frame carrying its debater identity + TRUE round on the wire
  // (parent is the ORIGINAL round-1 run — the star every revision points back at).
  const roundFrame = (
    runId: string,
    parentRunId: string,
    stance: "pro" | "con",
    roundNo: number,
    t: number,
  ): RunFrame => ({
    ...revised(runId, parentRunId, roundNo, t),
    stance,
    group: "debate:debate",
    round: roundNo,
  });

  it("projects wire stance/group/round onto the synthesized 修订 node", () => {
    const frames: RunFrame[] = [
      started("a-pro", "pro1"),
      started("a-con", "con1"),
      roundFrame("pro2", "pro1", "pro", 2, 3),
      roundFrame("con2", "con1", "con", 2, 4),
    ];
    const exec = projectExecution(debatePlan2, frames, "running");
    expect(exec.runs.find((r) => r.id === "pro2")).toMatchObject({
      stance: "pro",
      group: "debate:debate",
      round: 2,
      continuationIndex: 1,
      continuesRunId: "pro1",
    });
  });

  it("debateGroups now buckets rounds≥2 (root-fixes the live 2方 dropped-speech bug)", () => {
    const frames: RunFrame[] = [
      started("a-pro", "pro1"),
      started("a-con", "con1"),
      roundFrame("pro2", "pro1", "pro", 2, 3),
      roundFrame("con2", "con1", "con", 2, 4),
      roundFrame("pro3", "pro1", "pro", 3, 5),
      roundFrame("con3", "con1", "con", 3, 6),
    ];
    const groups = debateGroups(
      projectExecution(debatePlan2, frames, "running"),
    );
    expect(groups).toHaveLength(1);
    // All three rounds present — before the fix, rounds 2/3 were dropped (revisions
    // had no stance, so debateGroups never saw them → only round 1 rendered).
    expect(groups[0].rounds.map((r) => r.round)).toEqual([1, 2, 3]);
    expect(groups[0].rounds[1].pro.map((r) => r.id)).toEqual(["pro2"]);
    expect(groups[0].rounds[1].con.map((r) => r.id)).toEqual(["con2"]);
    expect(groups[0].rounds[2].pro.map((r) => r.id)).toEqual(["pro3"]);
  });
});
