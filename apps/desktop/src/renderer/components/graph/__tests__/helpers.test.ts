import { NODE_WIDTH } from "@/lib/elk-layout";
import type { Execution } from "@/stores/execution";
import { debateBeatLabel } from "@/stores/execution";
import { describe, expect, it } from "vitest";
import {
  type GraphRunLike,
  aggregateDebateRoundStatus,
  buildGraphStructure,
  computeGraphFold,
  debateModeratorId,
  debateRoundActiveBeat,
  debateRoundPhaseLabel,
  debateRoundSettledMark,
  hasActiveRunningWorkers,
  isCaptainKind,
  isDebateParticipantRun,
  pickDebateCrossExamActivateId,
  resolveCaptainSinkId,
  workerRunsOf,
} from "../helpers";
import {
  buildGraphScene,
  computeActBands,
  computeDebateStageBands,
  computeTopologicalRunWaves,
  computeWaves,
} from "../scene";

function run(
  id: string,
  deps: string[] = [],
  extra: Partial<GraphRunLike> = {},
): GraphRunLike {
  return { id, dependsOn: deps, ...extra };
}

describe("captain sink helpers", () => {
  it("resolveCaptainSinkId picks the first plan-order captain", () => {
    const runs = [
      { id: "cap", kind: "captain" as const },
      { id: "w1", kind: "agent" as const },
      { id: "cap2", kind: "captain" as const },
    ];
    expect(resolveCaptainSinkId(runs)).toBe("cap");
    expect(workerRunsOf(runs).map((r) => r.id)).toEqual(["w1"]);
    const [cap, worker] = runs;
    expect(cap).toBeDefined();
    expect(worker).toBeDefined();
    expect(isCaptainKind(cap)).toBe(true);
    expect(isCaptainKind(worker)).toBe(false);
  });

  it("hasActiveRunningWorkers ignores captain", () => {
    expect(
      hasActiveRunningWorkers([
        { kind: "captain", status: "running" },
        { kind: "agent", status: "pending" },
      ]),
    ).toBe(false);
    expect(
      hasActiveRunningWorkers([
        { kind: "captain", status: "running" },
        { kind: "agent", status: "running" },
      ]),
    ).toBe(true);
  });
});

function minimalExecution(
  runs: GraphRunLike[],
  captainId = "captain",
): Execution {
  return {
    runs: [
      {
        id: captainId,
        kind: "captain",
        dependsOn: [],
      } as unknown as Execution["runs"][0],
      ...runs.map(
        (r) =>
          ({
            id: r.id,
            dependsOn: r.dependsOn,
            parentRunId: r.parentRunId ?? null,
            continuationIndex: r.continuationIndex ?? 0,
            continuesRunId: r.continuesRunId ?? null,
            kind: "agent",
            actId: r.actId,
            stance: r.stance ?? null,
            group: r.group ?? null,
            receivedContext: r.receivedContext ?? [],
            delegateBatch: r.delegateBatch,
          }) as Execution["runs"][0],
      ),
    ],
  } as Execution;
}

describe("computeTopologicalRunWaves", () => {
  it("layers a linear dep chain", () => {
    const waves = computeTopologicalRunWaves(
      [run("a"), run("b", ["a"]), run("c", ["b"])],
      "captain",
    );
    expect(waves.get("a")).toBe(0);
    expect(waves.get("b")).toBe(1);
    expect(waves.get("c")).toBe(2);
  });

  it("groups parallel roots in wave 0", () => {
    const waves = computeTopologicalRunWaves(
      [run("a"), run("b"), run("c", ["a", "b"])],
      null,
    );
    expect(waves.get("a")).toBe(0);
    expect(waves.get("b")).toBe(0);
    expect(waves.get("c")).toBe(1);
  });

  it("puts delegate sub-tasks in the parent wave", () => {
    const waves = computeTopologicalRunWaves(
      [
        run("lead"),
        run("sub1", [], { parentRunId: "lead" }),
        run("sub2", [], { parentRunId: "lead" }),
        run("downstream", ["lead"]),
      ],
      "captain",
    );
    expect(waves.get("lead")).toBe(0);
    expect(waves.get("sub1")).toBe(0);
    expect(waves.get("sub2")).toBe(0);
    expect(waves.get("downstream")).toBe(1);
  });

  it("rolls sub-task external deps into the parent unit wave", () => {
    const waves = computeTopologicalRunWaves(
      [
        run("a"),
        run("lead", ["a"]),
        run("sub", ["a"], { parentRunId: "lead" }),
      ],
      null,
    );
    expect(waves.get("a")).toBe(0);
    expect(waves.get("lead")).toBe(1);
    expect(waves.get("sub")).toBe(1);
  });
});

describe("computeWaves", () => {
  it("returns no bands for a single wave", () => {
    const execution = minimalExecution([run("a"), run("b")]);
    const bands = computeWaves(
      execution,
      { a: { x: 0, y: 0 }, b: { x: 200, y: 0 } },
      { width: 400, height: 200 },
      "leftright",
      "captain",
    );
    expect(bands).toEqual([]);
  });

  it("labels topological waves as 批次 N", () => {
    const execution = minimalExecution([run("a"), run("b", ["a"])]);
    const bands = computeWaves(
      execution,
      { a: { x: 0, y: 0 }, b: { x: 300, y: 0 } },
      { width: 500, height: 200 },
      "leftright",
      "captain",
    );
    expect(bands).toHaveLength(2);
    expect(bands[0]?.label).toBe("批次 1");
    expect(bands[1]?.label).toBe("批次 2");
  });

  it("prefers 第 N 次委派 bands over topo waves when ≥2 delegate batches", () => {
    // Disjoint chains: batch1 a→b, batch2 c→d. Topo would group a+c / b+d and
    // mislabel them as「批次」; delegate bands must group a+b / c+d as rows.
    const execution = minimalExecution([
      run("a", [], { delegateBatch: 1 }),
      run("b", ["a"], { delegateBatch: 1 }),
      run("c", [], { delegateBatch: 2 }),
      run("d", ["c"], { delegateBatch: 2 }),
    ]);
    const bands = computeWaves(
      execution,
      {
        a: { x: 0, y: 0 },
        b: { x: 300, y: 0 },
        c: { x: 0, y: 140 },
        d: { x: 300, y: 140 },
      },
      { width: 520, height: 280 },
      "leftright",
      "captain",
    );
    expect(bands).toHaveLength(2);
    expect(bands[0]?.label).toBe("第 1 次委派");
    expect(bands[1]?.label).toBe("第 2 次委派");
    // Cross-axis (row) strips in leftright — not the topo column strips.
    expect(bands[0]?.h).toBeLessThan(bands[0]?.w ?? 0);
    expect(bands[1]?.y).toBeGreaterThan(bands[0]?.y ?? 0);
  });

  it("ignores pure continuations when batch2 is continuation-only", () => {
    // Cold a (batch1) + continuation a_v2 stamped batch2 must not open「第 2 次委派」.
    const execution = minimalExecution([
      run("a", [], { delegateBatch: 1 }),
      run("a_v2", [], {
        continuesRunId: "a",
        continuationIndex: 1,
        delegateBatch: 2,
      }),
    ]);
    const bands = computeWaves(
      execution,
      { a: { x: 0, y: 0 }, a_v2: { x: 300, y: 0 } },
      { width: 520, height: 200 },
      "leftright",
      "captain",
    );
    expect(bands).toEqual([]);
  });

  it("excludes continuations from 委派 members when cold + continue share a graph", () => {
    // Two cold batches plus a continuation on batch1 — continuation must not
    // appear in member lists or open an extra column.
    const execution = minimalExecution([
      run("a", [], { delegateBatch: 1 }),
      run("b", ["a"], { delegateBatch: 1 }),
      run("a_v2", [], {
        continuesRunId: "a",
        continuationIndex: 1,
        delegateBatch: 2,
      }),
      run("c", [], { delegateBatch: 2 }),
      run("d", ["c"], { delegateBatch: 2 }),
    ]);
    const bands = computeWaves(
      execution,
      {
        a: { x: 0, y: 0 },
        b: { x: 300, y: 0 },
        a_v2: { x: 600, y: 0 },
        c: { x: 0, y: 140 },
        d: { x: 300, y: 140 },
      },
      { width: 720, height: 280 },
      "leftright",
      "captain",
    );
    expect(bands).toHaveLength(2);
    expect(bands[0]?.label).toBe("第 1 次委派");
    expect(bands[1]?.label).toBe("第 2 次委派");
    const lanes = buildGraphScene(execution).bands.lanes;
    expect(lanes.map((b) => b.memberRunIds)).toEqual([
      ["a", "b"],
      ["c", "d"],
    ]);
    expect(lanes.flatMap((b) => b.memberRunIds)).not.toContain("a_v2");
  });
});

/** Execution 保真构造：保留 stance/group/round/receivedContext（minimalExecution 会剥掉）。 */
function debateStageExecution(
  runs: GraphRunLike[],
  captainId = "captain",
): Execution {
  return {
    runs: [{ id: captainId, kind: "captain", dependsOn: [] }, ...runs],
  } as unknown as Execution;
}

describe("computeDebateStageBands", () => {
  const positions = {
    mod: { x: 0, y: 120 },
    mod_r1_pro: { x: 260, y: 0 },
    mod_r1_con: { x: 260, y: 240 },
    mod_r2_pro: { x: 520, y: 0 },
    mod_r2_con: { x: 520, y: 240 },
    mod_closing_pro: { x: 780, y: 0 },
    mod_closing_con: { x: 780, y: 240 },
  };

  it("returns [] for non-debate executions", () => {
    const exec = debateStageExecution([
      { id: "a", dependsOn: [] },
      { id: "b", dependsOn: [] },
    ]);
    expect(
      computeDebateStageBands(
        exec,
        { a: { x: 0, y: 0 }, b: { x: 200, y: 0 } },
        "captain",
      ),
    ).toEqual([]);
  });

  it("partitions multibeat debate into 第1轮/第2轮/结辩 left-to-right", () => {
    const exec = debateStageExecution(debateMultibeatRuns());
    const bands = computeDebateStageBands(exec, positions, "captain");
    expect(bands.map((b) => b.label)).toEqual(["第 1 轮", "第 2 轮", "结辩"]);
    expect(bands[0]?.x).toBeLessThan(bands[1]?.x ?? 0);
    expect(bands[1]?.x).toBeLessThan(bands[2]?.x ?? 0);
  });

  it("anchors 第1轮 label on the debater column (ignores moderator x)", () => {
    const exec = debateStageExecution(debateMultibeatRuns());
    const bands = computeDebateStageBands(exec, positions, "captain");
    const first = bands[0];
    const second = bands[1];
    // 第1轮只含辩手列 x=260，与第2轮同宽；标签居中于辩手列，不因主持(x=0)左偏。
    expect(first?.w).toBe(second?.w);
    expect(first?.labelX).toBe(260 + NODE_WIDTH / 2);
    expect(second?.labelX).toBe(520 + NODE_WIDTH / 2);
  });

  it("never turns cross-exam into its own stage (3 stages only)", () => {
    const exec = debateStageExecution(debateMultibeatRuns());
    const bands = computeDebateStageBands(exec, positions, "captain");
    expect(bands).toHaveLength(3);
  });

  it("labels a single statement round as 第1轮", () => {
    const single: GraphRunLike[] = [
      { id: "mod", dependsOn: [], parentRunId: null },
      {
        id: "mod_r1_pro",
        dependsOn: [],
        parentRunId: "mod",
        continuesRunId: null,
        stance: "pro",
        group: "debate:debate",
        round: 1,
      },
      {
        id: "mod_r1_con",
        dependsOn: [],
        parentRunId: "mod",
        continuesRunId: null,
        stance: "con",
        group: "debate:debate",
        round: 1,
      },
    ];
    const bands = computeDebateStageBands(
      debateStageExecution(single),
      {
        mod: { x: 0, y: 120 },
        mod_r1_pro: { x: 260, y: 0 },
        mod_r1_con: { x: 260, y: 240 },
      },
      "captain",
    );
    expect(bands.map((b) => b.label)).toEqual(["第 1 轮"]);
  });
});

function debateSide(
  prefix: string,
  stance: "pro" | "con",
  rounds: number,
): GraphRunLike[] {
  const original: GraphRunLike = {
    id: `mod_r1_${prefix}`,
    dependsOn: [],
    parentRunId: "mod",
    continuationIndex: 0,
    continuesRunId: null,
    stance,
    group: "debate:debate",
  };
  const revs: GraphRunLike[] = [];
  for (let r = 2; r <= rounds; r++) {
    revs.push({
      id: `mod_r${r}_${prefix}`,
      dependsOn: [],
      parentRunId: original.id,
      continuationIndex: r - 1,
      continuesRunId: original.id,
      stance,
      group: "debate:debate",
    });
  }
  return [original, ...revs];
}

/** 多轮对抗 + 每轮质询 + 结辩：钉死协作图列数（每方 轮数+1 结辩）与修订链。 */
function debateMultibeatSide(
  prefix: string,
  stance: "pro" | "con",
): GraphRunLike[] {
  const original: GraphRunLike = {
    id: `mod_r1_${prefix}`,
    dependsOn: [],
    parentRunId: "mod",
    continuationIndex: 0,
    continuesRunId: null,
    stance,
    group: "debate:debate",
    round: 1,
  };
  return [
    original,
    {
      id: `mod_r1_cx_${prefix}`,
      dependsOn: [],
      parentRunId: original.id,
      continuationIndex: 1,
      continuesRunId: original.id,
      stance,
      group: "debate:debate",
      round: 1,
      receivedContext: [{ channel: "cross_exam" }],
    },
    {
      id: `mod_r2_${prefix}`,
      dependsOn: [],
      parentRunId: original.id,
      continuationIndex: 2,
      continuesRunId: original.id,
      stance,
      group: "debate:debate",
      round: 2,
    },
    {
      id: `mod_r2_cx_${prefix}`,
      dependsOn: [],
      parentRunId: original.id,
      continuationIndex: 3,
      continuesRunId: original.id,
      stance,
      group: "debate:debate",
      round: 2,
      receivedContext: [{ channel: "cross_exam" }],
    },
    {
      id: `mod_closing_${prefix}`,
      dependsOn: [],
      parentRunId: original.id,
      continuationIndex: 4,
      continuesRunId: original.id,
      stance,
      group: "debate:debate",
      round: 2,
      receivedContext: [{ channel: "closing" }],
    },
  ];
}

function debateRuns(rounds: number): GraphRunLike[] {
  return [
    { id: "mod", dependsOn: [], parentRunId: null, kind: "agent" },
    ...debateSide("pro", "pro", rounds),
    ...debateSide("con", "con", rounds),
  ];
}

function debateMultibeatRuns(): GraphRunLike[] {
  return [
    { id: "mod", dependsOn: [], parentRunId: null, kind: "agent" },
    ...debateMultibeatSide("pro", "pro"),
    ...debateMultibeatSide("con", "con"),
  ];
}

describe("computeGraphFold · debate compound", () => {
  it("folds all debater runs under the moderator unit", () => {
    const runs = debateRuns(4);
    expect(debateModeratorId(runs, null)).toBe("mod");
    const fold = computeGraphFold(runs, null);
    expect(fold.debateUnits.has("mod")).toBe(true);
    expect(fold.folded.size).toBe(8);
    expect(fold.unitOf.get("mod_r1_pro")).toBe("mod");
    expect(fold.unitOf.get("mod_r4_con")).toBe("mod");
  });

  it("does not promote non-participant children under debaters to debateUnits", () => {
    // 白名单外 group 挂在主辩下时，主辩不得晋升独立单元 → 假分带。
    const runs: GraphRunLike[] = [
      { id: "mod", dependsOn: [], parentRunId: null, kind: "agent" },
      {
        id: "mod_r1_pro",
        dependsOn: [],
        parentRunId: "mod",
        stance: "pro",
        group: "debate:debate",
      },
      {
        id: "mod_r1_con",
        dependsOn: [],
        parentRunId: "mod",
        stance: "con",
        group: "debate:debate",
      },
      {
        id: "aux_pro",
        dependsOn: [],
        parentRunId: "mod_r1_pro",
        group: "other:aux:pro",
      },
      {
        id: "aux_con",
        dependsOn: [],
        parentRunId: "mod_r1_con",
        group: "other:aux:con",
      },
    ];
    const fold = computeGraphFold(runs, null);
    expect(fold.debateUnits.has("mod")).toBe(true);
    expect(fold.debateUnits.has("mod_r1_pro")).toBe(false);
    expect(fold.debateUnits.has("mod_r1_con")).toBe(false);
    expect(fold.unitOf.get("mod_r1_pro")).toBe("mod");
    expect(fold.unitOf.get("aux_pro")).toBe("mod");
    expect(fold.unitOf.get("aux_con")).toBe("mod");
    const { subTeams } = buildGraphStructure(runs, "__input__");
    expect(subTeams).toHaveLength(1);
    expect(subTeams[0]?.parentId).toBe("mod");
    expect(subTeams[0]?.memberIds).toEqual(
      expect.arrayContaining([
        "mod_r1_pro",
        "mod_r1_con",
        "aux_pro",
        "aux_con",
      ]),
    );
  });

  it("rejects non-whitelist debate:* groups as participants (defense in depth)", () => {
    const aux: GraphRunLike = {
      id: "aux",
      dependsOn: [],
      parentRunId: "mod_r1_pro",
      group: "debate:other:pro",
    };
    expect(isDebateParticipantRun(aux)).toBe(false);
    const runs: GraphRunLike[] = [
      { id: "mod", dependsOn: [], parentRunId: null, kind: "agent" },
      {
        id: "mod_r1_pro",
        dependsOn: [],
        parentRunId: "mod",
        stance: "pro",
        group: "debate:debate",
      },
      aux,
    ];
    const fold = computeGraphFold(runs, null);
    expect(fold.debateUnits.has("mod")).toBe(true);
    expect(fold.debateUnits.has("mod_r1_pro")).toBe(false);
  });

  it("always expands debate grid without requiring expandedUnits", () => {
    const { nodeIds, subTeams } = buildGraphStructure(
      debateRuns(4),
      "__input__",
    );
    expect(nodeIds).toContain("mod");
    expect(nodeIds).toContain("mod_r1_pro");
    expect(nodeIds).toContain("mod_r4_con");
    const debateTeam = subTeams.find((t) => t.parentId === "mod");
    expect(debateTeam?.memberIds).toEqual(
      expect.arrayContaining([
        "mod_r1_pro",
        "mod_r1_con",
        "mod_r4_pro",
        "mod_r4_con",
      ]),
    );
  });

  it("folds cross-exam into same-round statement; closing stays a column (multibeat)", () => {
    const runs = debateMultibeatRuns();
    const { nodeIds, rawEdges, subTeams } = buildGraphStructure(
      runs,
      "__input__",
    );
    // 每方 3 列：首轮陈词（含折进的质询）+ 第2轮陈词（含质询）+ 结辩。
    const debateTeam = subTeams.find((t) => t.parentId === "mod");
    expect(debateTeam?.memberIds).toHaveLength(6);
    for (const id of [
      "mod_r1_pro",
      "mod_r2_pro",
      "mod_closing_pro",
      "mod_r1_con",
      "mod_r2_con",
      "mod_closing_con",
    ]) {
      expect(nodeIds).toContain(id);
      expect(debateTeam?.memberIds).toContain(id);
    }
    for (const id of [
      "mod_r1_cx_pro",
      "mod_r2_cx_pro",
      "mod_r1_cx_con",
      "mod_r2_cx_con",
    ]) {
      expect(nodeIds).not.toContain(id);
      expect(debateTeam?.memberIds).not.toContain(id);
    }
    // 修订链：轮→轮→结辩，无质询 phantom。
    const revEdges = rawEdges
      .filter((e) => e.kind === "continuation")
      .map((e) => `${e.source}->${e.target}`)
      .sort();
    expect(revEdges).toEqual(
      [
        "mod_r1_con->mod_r2_con",
        "mod_r1_pro->mod_r2_pro",
        "mod_r2_con->mod_closing_con",
        "mod_r2_pro->mod_closing_pro",
      ].sort(),
    );
    // 侧栏仍可辨识 beat 文案；图上质询角标随独立节点消失。
    expect(debateBeatLabel({ round: 1, beat: "cross_exam" })).toBe(
      "第 1 轮·质询",
    );
    expect(debateBeatLabel({ round: 2, beat: "statement" })).toBe("第 2 轮");
    expect(debateBeatLabel({ round: 2, beat: "closing" })).toBe("结辩");
  });
});

describe("computeGraphFold · multi-act (MLR + debate)", () => {
  function mlrDebateRuns(): GraphRunLike[] {
    return [
      run("lens_0", [], { actId: "act-1" }),
      run("synthesizer", ["lens_0"], { actId: "act-1" }),
      run("mod", [], {
        actId: "act-2",
        parentRunId: "synthesizer",
      }),
      run("mod_r1_pro", [], {
        actId: "act-2",
        parentRunId: "mod",
        stance: "pro",
        group: "debate:debate",
      }),
      run("mod_r1_con", [], {
        actId: "act-2",
        parentRunId: "mod",
        stance: "con",
        group: "debate:debate",
      }),
    ];
  }

  it("does not fold debate moderator into synthesizer unit", () => {
    const runs = mlrDebateRuns();
    const fold = computeGraphFold(runs, null);
    expect(fold.debateUnits.has("mod")).toBe(true);
    expect(fold.unitOf.get("mod")).toBe("mod");
    expect(fold.folded.has("mod")).toBe(false);
    expect(fold.unitOf.get("mod_r1_pro")).toBe("mod");
    expect(fold.unitOf.get("synthesizer")).toBe("synthesizer");
  });

  it("keeps debaters in moderator sub-team, not synthesizer box", () => {
    const { nodeIds, rawEdges, subTeams } = buildGraphStructure(
      mlrDebateRuns(),
      "__input__",
    );
    expect(nodeIds).toContain("mod");
    expect(nodeIds).toContain("mod_r1_pro");
    expect(nodeIds).toContain("synthesizer");
    const synTeam = subTeams.find((t) => t.parentId === "synthesizer");
    expect(synTeam).toBeUndefined();
    const debateTeam = subTeams.find((t) => t.parentId === "mod");
    expect(debateTeam?.memberIds).toEqual(
      expect.arrayContaining(["mod_r1_pro", "mod_r1_con"]),
    );
    expect(debateTeam?.memberIds).not.toContain("mod");
    const bridge = rawEdges.find(
      (e) => e.source === "synthesizer" && e.target === "mod",
    );
    expect(bridge).toBeDefined();
    expect(bridge?.kind).toBe("dep");
    // 幕2 入口不从 input 扇出
    expect(
      rawEdges.some((e) => e.source === "__input__" && e.target === "mod"),
    ).toBe(false);
  });
});

describe("computeActBands", () => {
  it("returns empty for single-act graphs", () => {
    const exec = minimalExecution([run("a"), run("b", ["a"])]);
    exec.acts = [
      {
        actId: "act-1",
        kind: "multi_agent",
        title: "调研",
        anchorRunId: null,
        authorizedBy: null,
      },
    ];
    expect(
      computeActBands(
        exec,
        { a: { x: 0, y: 0 }, b: { x: 200, y: 0 } },
        "captain",
      ),
    ).toEqual([]);
  });

  it("bands by act title for mixed graphs", () => {
    const exec = minimalExecution([
      run("lens", [], { actId: "act-1" }),
      run("syn", ["lens"], { actId: "act-1" }),
      run("mod", [], { actId: "act-2", parentRunId: "syn" }),
      run("pro", [], {
        actId: "act-2",
        parentRunId: "mod",
        stance: "pro",
        group: "debate:debate",
      }),
    ]);
    exec.acts = [
      {
        actId: "act-1",
        kind: "multi_agent",
        title: "多视角调研",
        anchorRunId: null,
        authorizedBy: null,
      },
      {
        actId: "act-2",
        kind: "debate",
        title: "辩论对抗",
        anchorRunId: "syn",
        authorizedBy: "stage_card",
      },
    ];
    const bands = computeActBands(
      exec,
      {
        lens: { x: 0, y: 0 },
        syn: { x: 200, y: 0 },
        mod: { x: 400, y: 0 },
        pro: { x: 400, y: 120 },
      },
      "captain",
    );
    expect(bands.map((b) => b.label)).toEqual([
      "多视角调研",
      "辩论对抗 · 经推进卡授权",
    ]);
    expect(
      computeWaves(exec, {}, { width: 100, height: 100 }, "leftright", null),
    ).toEqual([]);
  });

  it("uses topological columns with bbox (leftright) instead of tall AABB", () => {
    const exec = minimalExecution([
      run("lens", [], { actId: "act-1" }),
      run("syn", ["lens"], { actId: "act-1" }),
      run("mod", [], { actId: "act-2", parentRunId: "syn" }),
      run("pro", [], {
        actId: "act-2",
        parentRunId: "mod",
        stance: "pro",
        group: "debate:debate",
      }),
      run("con", [], {
        actId: "act-2",
        parentRunId: "mod",
        stance: "con",
        group: "debate:debate",
      }),
    ]);
    exec.acts = [
      {
        actId: "act-1",
        kind: "multi_agent",
        title: "调研",
        anchorRunId: null,
        authorizedBy: null,
      },
      {
        actId: "act-2",
        kind: "debate",
        title: "辩论",
        anchorRunId: "syn",
        authorizedBy: "auto",
      },
    ];
    // 长辩论树：辩手 y 跨度很大；调研与辩论 x 分列。
    const positions = {
      lens: { x: 0, y: 40 },
      syn: { x: 200, y: 40 },
      mod: { x: 420, y: 0 },
      pro: { x: 420, y: 200 },
      con: { x: 420, y: 800 },
    };
    const bbox = { width: 600, height: 900 };
    const bands = computeActBands(
      exec,
      positions,
      "captain",
      bbox,
      "leftright",
    );
    expect(bands).toHaveLength(2);
    expect(bands[1].label).toContain("自动开辩");
    // 拓扑竖条：两幕同贴 bbox 高（并排列），而非各包自己的 AABB 高矮不一。
    expect(bands[0].h).toBeCloseTo(bands[1].h, 0);
    expect(bands[0].h).toBeGreaterThan(bbox.height);
    // 调研列在左、辩论列在右（中心序）。
    expect(bands[0].x + bands[0].w / 2).toBeLessThan(
      bands[1].x + bands[1].w / 2,
    );
    // AABB 回落：辩论幕带跟节点 y 跨度走（与调研幕带高度差巨大）；列模式两幕同高。
    const aabb = computeActBands(exec, positions, "captain");
    expect(aabb[1].h - aabb[0].h).toBeGreaterThan(500);
    expect(Math.abs(bands[1].h - bands[0].h)).toBeLessThan(1);
  });
});

describe("debate beat fold helpers", () => {
  it("folds rebuttal/crux like cross-exam; attack/defense/thread stay hosts", () => {
    const attack = {
      id: "mod_r1_red1",
      dependsOn: [] as string[],
      group: "debate:red_team",
      round: 1,
      receivedContext: [{ channel: "attack" }],
      status: "completed" as const,
    };
    const rebuttal = {
      id: "mod_r1_red1_rebuttal",
      dependsOn: [] as string[],
      continuesRunId: "mod_r1_red1",
      group: "debate:red_team",
      round: 1,
      receivedContext: [{ channel: "rebuttal" }],
      status: "completed" as const,
    };
    const defense = {
      id: "mod_r1_subject_defense",
      dependsOn: [] as string[],
      group: "debate:red_team",
      round: 1,
      receivedContext: [{ channel: "defense" }],
      status: "completed" as const,
    };
    const { nodeIds } = buildGraphStructure(
      [attack, rebuttal, defense],
      "__input__",
    );
    expect(nodeIds).toContain("mod_r1_red1");
    expect(nodeIds).toContain("mod_r1_subject_defense");
    expect(nodeIds).not.toContain("mod_r1_red1_rebuttal");
    expect(debateBeatLabel({ round: 1, beat: "attack" })).toBe("第 1 轮·攻击");
    expect(debateBeatLabel({ round: 1, beat: "defense" })).toBe("第 1 轮·回应");
    expect(debateBeatLabel({ round: 1, beat: "rebuttal" })).toBe(
      "第 1 轮·复攻",
    );
  });

  it("aggregates status with running/failed over completed", () => {
    expect(aggregateDebateRoundStatus(["completed", "running"])).toBe(
      "running",
    );
    expect(aggregateDebateRoundStatus(["completed", "failed"])).toBe("failed");
    expect(aggregateDebateRoundStatus(["completed", "completed"])).toBe(
      "completed",
    );
  });

  it("labels live phase as 质询作答中 when CX is active", () => {
    expect(debateRoundActiveBeat("completed", ["running"])).toBe("cross_exam");
    expect(debateRoundPhaseLabel("running", "cross_exam", true)).toBe(
      "质询作答中",
    );
    expect(debateRoundPhaseLabel("running", "statement", true)).toBe("立论中");
    expect(debateRoundPhaseLabel("completed", "statement", true)).toBeNull();
  });

  it("settled mark: 含质询 on completed, 质询作答失败 when CX failed", () => {
    expect(debateRoundSettledMark("completed", true, ["completed"])).toEqual({
      label: "含质询",
      mode: "suffix",
    });
    expect(debateRoundSettledMark("failed", true, ["failed"])).toEqual({
      label: "质询作答失败",
      mode: "replace",
    });
    expect(debateRoundSettledMark("failed", true, ["completed"])).toBeNull();
    expect(debateRoundSettledMark("running", true, ["running"])).toBeNull();
    expect(debateRoundSettledMark("completed", false, [])).toBeNull();
    expect(
      debateRoundSettledMark("completed", true, ["completed"], ["rebuttal"]),
    ).toEqual({ label: "含复攻", mode: "suffix" });
    expect(
      debateRoundSettledMark("completed", true, ["completed"], ["crux"]),
    ).toEqual({ label: "含 crux", mode: "suffix" });
  });

  it("picks CX activate id: active > failed > latest", () => {
    expect(
      pickDebateCrossExamActivateId([
        { id: "a", status: "completed" },
        { id: "b", status: "running" },
        { id: "c", status: "failed" },
      ]),
    ).toBe("b");
    expect(
      pickDebateCrossExamActivateId([
        { id: "a", status: "completed" },
        { id: "b", status: "failed" },
        { id: "c", status: "completed" },
      ]),
    ).toBe("b");
    expect(
      pickDebateCrossExamActivateId([
        { id: "a", status: "completed" },
        { id: "b", status: "completed" },
      ]),
    ).toBe("b");
    expect(pickDebateCrossExamActivateId([])).toBeNull();
  });
});

describe("buildGraphStructure · bookend sink edges", () => {
  const captain = (): GraphRunLike => ({
    id: "captain",
    dependsOn: [],
    kind: "captain",
  });

  const sinkTargets = (edges: { source: string; target: string }[]) =>
    edges
      .filter((e) => e.target === "captain")
      .map((e) => e.source)
      .sort();

  it("fans parallel leaves into the CEO", () => {
    const { rawEdges } = buildGraphStructure(
      [captain(), run("w1"), run("w2"), run("w3")],
      "__input__",
    );
    expect(sinkTargets(rawEdges)).toEqual(["w1", "w2", "w3"]);
    expect(
      rawEdges.filter((e) => e.source === "__input__").map((e) => e.target),
    ).toEqual(expect.arrayContaining(["w1", "w2", "w3"]));
  });

  it("connects only the serial chain tip to the CEO", () => {
    const { rawEdges } = buildGraphStructure(
      [captain(), run("s1"), run("s2", ["s1"]), run("s3", ["s2"])],
      "__input__",
    );
    expect(sinkTargets(rawEdges)).toEqual(["s3"]);
    expect(
      rawEdges.some((e) => e.source === "__input__" && e.target === "s1"),
    ).toBe(true);
  });

  it("connects the debate moderator unit to the CEO", () => {
    const { rawEdges } = buildGraphStructure(
      [captain(), ...debateRuns(2)],
      "__input__",
    );
    expect(sinkTargets(rawEdges)).toEqual(["mod"]);
  });

  it("补派 replaces_run_id：接替边 + 失败节点不再汇入 CEO + 补派不挂 input", () => {
    const { rawEdges } = buildGraphStructure(
      [
        captain(),
        run("w1"),
        run("w2"),
        run("w1b", [], { replacesRunId: "w1" }),
      ],
      "__input__",
    );
    expect(
      rawEdges.some(
        (e) => e.kind === "handoff" && e.source === "w1" && e.target === "w1b",
      ),
    ).toBe(true);
    expect(sinkTargets(rawEdges)).toEqual(["w1b", "w2"]);
    expect(
      rawEdges.some((e) => e.source === "__input__" && e.target === "w1b"),
    ).toBe(false);
    expect(
      rawEdges.some((e) => e.source === "__input__" && e.target === "w1"),
    ).toBe(true);
  });

  it("补派后下游 depends_on 改写指向新 run：主干依赖边自然成立", () => {
    const { rawEdges } = buildGraphStructure(
      [
        captain(),
        run("w1"),
        run("w1b", [], { replacesRunId: "w1" }),
        run("w2", ["w1b"]),
      ],
      "__input__",
    );
    expect(
      rawEdges.some(
        (e) => e.kind === "dep" && e.source === "w1b" && e.target === "w2",
      ),
    ).toBe(true);
    expect(sinkTargets(rawEdges)).toEqual(["w2"]);
    expect(
      rawEdges.some((e) => e.source === "w1" && e.target === "captain"),
    ).toBe(false);
  });

  it("续链：只根接 input、只链尖汇 CEO；continuation 点线保留", () => {
    const { rawEdges } = buildGraphStructure(
      [
        captain(),
        run("w1"),
        run("w1_v2", [], {
          continuesRunId: "w1",
          continuationIndex: 1,
        }),
        run("w1_v3", [], {
          continuesRunId: "w1",
          continuationIndex: 2,
        }),
      ],
      "__input__",
    );
    const inputTargets = rawEdges
      .filter((e) => e.source === "__input__")
      .map((e) => e.target)
      .sort();
    expect(inputTargets).toEqual(["w1"]);
    expect(sinkTargets(rawEdges)).toEqual(["w1_v3"]);
    expect(
      rawEdges
        .filter((e) => e.kind === "continuation")
        .map((e) => `${e.source}->${e.target}`)
        .sort(),
    ).toEqual(["w1->w1_v2", "w1_v2->w1_v3"]);
    expect(
      rawEdges.some((e) => e.source === "w1" && e.target === "captain"),
    ).toBe(false);
    expect(
      rawEdges.some((e) => e.source === "w1_v2" && e.target === "captain"),
    ).toBe(false);
  });

  it("续链上有真实 dependsOn 的续仍画 peer dep", () => {
    const { rawEdges } = buildGraphStructure(
      [
        captain(),
        run("a"),
        run("a_v2", [], {
          continuesRunId: "a",
          continuationIndex: 1,
        }),
        run("b", ["a_v2"]),
      ],
      "__input__",
    );
    expect(
      rawEdges.some(
        (e) => e.kind === "dep" && e.source === "a_v2" && e.target === "b",
      ),
    ).toBe(true);
    expect(
      rawEdges.some(
        (e) =>
          e.kind === "continuation" && e.source === "a" && e.target === "a_v2",
      ),
    ).toBe(true);
    // a 有续后继不进 CEO；a_v2 被 b dependsOn，也不进；仅 b → captain。
    expect(sinkTargets(rawEdges)).toEqual(["b"]);
    expect(
      rawEdges.filter((e) => e.source === "__input__").map((e) => e.target),
    ).toEqual(["a"]);
  });

  it("drops append-turn captains — no fake CEO 子队 / delegate edge", () => {
    // Cross-turn graph_append leaves an extra kind=captain in runs; parent_run_id
    // of the new worker points at that captain. Must not render as worker/sub-team.
    const { nodeIds, rawEdges, subTeams } = buildGraphStructure(
      [
        captain(),
        run("greeter", [], {
          parentRunId: "captain",
          delegateBatch: 1,
        }),
        {
          id: "cap_append",
          dependsOn: [],
          kind: "captain",
        },
        run("newbie", [], {
          parentRunId: "cap_append",
          delegateBatch: 2,
        }),
      ],
      "__input__",
    );
    expect(nodeIds).not.toContain("cap_append");
    expect(nodeIds).toEqual(
      expect.arrayContaining(["greeter", "newbie", "__input__", "captain"]),
    );
    // Extra captain must never become a sub-team parent (would paint「CEO 子队」).
    expect(subTeams).toEqual([]);
    expect(subTeams.every((st) => st.parentId !== "cap_append")).toBe(true);
    expect(
      rawEdges.some(
        (e) =>
          e.kind === "delegate" &&
          (e.source === "cap_append" || e.target === "cap_append"),
      ),
    ).toBe(false);
    expect(sinkTargets(rawEdges).sort()).toEqual(["greeter", "newbie"]);
    const scene = buildGraphScene({
      runs: [
        { id: "captain", kind: "captain", dependsOn: [] },
        {
          id: "greeter",
          dependsOn: [],
          parentRunId: "captain",
          delegateBatch: 1,
        },
        { id: "cap_append", kind: "captain", dependsOn: [] },
        {
          id: "newbie",
          dependsOn: [],
          parentRunId: "cap_append",
          delegateBatch: 2,
        },
      ],
    } as unknown as Execution);
    expect(scene.captainId).toBe("captain");
    expect(scene.nodeIds).not.toContain("cap_append");
    expect(scene.subTeams).toEqual([]);
    expect(scene.bands.lanes.flatMap((b) => b.memberRunIds)).not.toContain(
      "cap_append",
    );
  });
});
