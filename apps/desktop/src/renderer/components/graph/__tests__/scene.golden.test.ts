/**
 * GraphScene golden — locks the structural conclusions (fold / attribution /
 * bands / acts) the whole renderer depends on. Fixtures reuse the run shapes
 * from the existing graph tests (debate multibeat, multi-act MLR, nested
 * delegation, continuation chains, multi-delegate). A structural bug (phantom
 * column, escaped revision, wrong beat host, band drift) now shows up here in
 * the data layer, before any ELK / ReactFlow rendering.
 */
import type { Execution, ExecutionAct, RunStatus } from "@/stores/execution";
import { describe, expect, it } from "vitest";
import { INPUT_ID } from "../ids";
import { type GraphScene, buildGraphScene } from "../scene";

interface RunSpec {
  id: string;
  dependsOn?: string[];
  parentRunId?: string | null;
  continuesRunId?: string | null;
  continuationIndex?: number;
  replacesRunId?: string | null;
  stance?: "pro" | "con" | null;
  group?: string | null;
  round?: number;
  kind?: string;
  actId?: string;
  delegateBatch?: number;
  receivedContext?: { channel: string }[];
  status?: RunStatus;
  role?: string;
  agentId?: string;
  durationMs?: number;
}

function mkExec(specs: RunSpec[], acts?: ExecutionAct[]): Execution {
  const runs = specs.map((s) => ({
    id: s.id,
    agentId: s.agentId ?? s.id,
    role: s.role ?? null,
    dependsOn: s.dependsOn ?? [],
    parentRunId: s.parentRunId ?? null,
    continuesRunId: s.continuesRunId ?? null,
    continuationIndex: s.continuationIndex ?? 0,
    replacesRunId: s.replacesRunId ?? null,
    stance: s.stance ?? null,
    group: s.group ?? null,
    round: s.round ?? 0,
    kind: s.kind ?? "agent",
    actId: s.actId,
    delegateBatch: s.delegateBatch,
    receivedContext: s.receivedContext ?? [],
    status: s.status ?? "pending",
    durationMs: s.durationMs ?? null,
    escalations: [],
    checkpoint: null,
  }));
  return { runs, acts: acts ?? [], agents: [] } as unknown as Execution;
}

const captain: RunSpec = { id: "captain", kind: "captain" };

/** Stable, human-readable projection of the scene for snapshotting. */
function snapshotScene(scene: GraphScene) {
  const sortedMap = (m: Map<string, string | null>) =>
    Object.fromEntries(
      [...m.entries()].sort((a, b) => a[0].localeCompare(b[0])),
    );
  const sortedListMap = (m: Map<string, string[]>) =>
    Object.fromEntries(
      [...m.entries()]
        .map(([k, v]) => [k, [...v]] as const)
        .sort((a, b) => a[0].localeCompare(b[0])),
    );
  return {
    acts: scene.acts.map((a) => ({
      actId: a.actId,
      kind: a.kind,
      title: a.title,
      authorizedBy: a.authorizedBy,
      unitIds: a.unitIds,
    })),
    units: scene.units.map((u) => ({
      id: u.id,
      actId: u.actId,
      debate: u.debate,
      groupId: u.groupId,
      memberIds: u.memberIds,
    })),
    edges: scene.edges
      .map((e) => `${e.kind ?? "dep"}:${e.source}->${e.target}`)
      .sort(),
    lanes: scene.bands.lanes.map((b) => ({
      id: b.id,
      kind: b.kind,
      label: b.label,
      memberRunIds: b.memberRunIds,
    })),
    debateStages: scene.bands.debateStages.map((b) => ({
      id: b.id,
      label: b.label,
      memberRunIds: b.memberRunIds,
    })),
    subTeams: scene.subTeams
      .map((st) => ({
        parentId: st.parentId,
        groupId: st.groupId,
        memberIds: st.memberIds,
      }))
      .sort((a, b) => a.parentId.localeCompare(b.parentId)),
    fold: {
      folded: [...scene.fold.folded].sort(),
      debateUnits: [...scene.fold.debateUnits].sort(),
      unitOf: sortedMap(scene.fold.unitOf),
      descendants: sortedListMap(scene.fold.descendants),
    },
    nodeGroup: sortedMap(scene.nodeGroup),
    beatFoldsByHost: sortedListMap(scene.beatFoldsByHost),
  };
}

// ── Fixtures ────────────────────────────────────────────────────────────────

/** Multi-delegate: two disjoint chains stamped with different delegate batches. */
function multiDelegateExec(): Execution {
  return mkExec([
    captain,
    { id: "a", delegateBatch: 1 },
    { id: "b", dependsOn: ["a"], delegateBatch: 1 },
    { id: "c", delegateBatch: 2 },
    { id: "d", dependsOn: ["c"], delegateBatch: 2 },
  ]);
}

/** Multi-delegate with a pure continuation stamped on batch2 (must not join lanes). */
function multiDelegateWithContinuationExec(): Execution {
  return mkExec([
    captain,
    { id: "a", delegateBatch: 1 },
    { id: "b", dependsOn: ["a"], delegateBatch: 1 },
    {
      id: "a_v2",
      continuesRunId: "a",
      continuationIndex: 1,
      delegateBatch: 2,
    },
    { id: "c", delegateBatch: 2 },
    { id: "d", dependsOn: ["c"], delegateBatch: 2 },
  ]);
}

/** Same-person continuation chain (hotfix v2/v3) staying top-level (non-debate). */
function continuationChainExec(): Execution {
  return mkExec([
    captain,
    { id: "w1" },
    { id: "w1_v2", continuesRunId: "w1", continuationIndex: 1 },
    { id: "w1_v3", continuesRunId: "w1", continuationIndex: 2 },
  ]);
}

/** Nested delegation: mpm ⇢ lead ⇢ {eng1, eng2}. */
function nestedSubteamExec(): Execution {
  return mkExec([
    captain,
    { id: "mpm" },
    { id: "lead", parentRunId: "mpm" },
    { id: "eng1", parentRunId: "lead" },
    { id: "eng2", parentRunId: "lead" },
  ]);
}

/** Debate side: statement + folded cross-exam per round, then closing. */
function debateMultibeatSide(prefix: string, stance: "pro" | "con"): RunSpec[] {
  const orig = `mod_r1_${prefix}`;
  return [
    {
      id: orig,
      parentRunId: "mod",
      continuationIndex: 0,
      stance,
      group: "debate:debate",
      round: 1,
    },
    {
      id: `mod_r1_cx_${prefix}`,
      parentRunId: orig,
      continuesRunId: orig,
      continuationIndex: 1,
      stance,
      group: "debate:debate",
      round: 1,
      receivedContext: [{ channel: "cross_exam" }],
    },
    {
      id: `mod_r2_${prefix}`,
      parentRunId: orig,
      continuesRunId: orig,
      continuationIndex: 2,
      stance,
      group: "debate:debate",
      round: 2,
    },
    {
      id: `mod_r2_cx_${prefix}`,
      parentRunId: orig,
      continuesRunId: orig,
      continuationIndex: 3,
      stance,
      group: "debate:debate",
      round: 2,
      receivedContext: [{ channel: "cross_exam" }],
    },
    {
      id: `mod_closing_${prefix}`,
      parentRunId: orig,
      continuesRunId: orig,
      continuationIndex: 4,
      stance,
      group: "debate:debate",
      round: 2,
      receivedContext: [{ channel: "closing" }],
    },
  ];
}

function debateMultibeatExec(): Execution {
  return mkExec([
    captain,
    { id: "mod", parentRunId: null },
    ...debateMultibeatSide("pro", "pro"),
    ...debateMultibeatSide("con", "con"),
  ]);
}

/** Multi-act (≥2): act-1 research → synthesizer; act-2 debate under synthesizer. */
function multiActExec(): Execution {
  const acts: ExecutionAct[] = [
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
      anchorRunId: "synthesizer",
      authorizedBy: "stage_card",
    },
  ];
  return mkExec(
    [
      captain,
      { id: "lens_0", actId: "act-1" },
      { id: "synthesizer", dependsOn: ["lens_0"], actId: "act-1" },
      { id: "mod", parentRunId: "synthesizer", actId: "act-2" },
      {
        id: "mod_r1_pro",
        parentRunId: "mod",
        actId: "act-2",
        stance: "pro",
        group: "debate:debate",
      },
      {
        id: "mod_r1_con",
        parentRunId: "mod",
        actId: "act-2",
        stance: "con",
        group: "debate:debate",
      },
    ],
    acts,
  );
}

// ── Golden snapshots ────────────────────────────────────────────────────────

describe("buildGraphScene · golden", () => {
  it("multi-delegate → two 委派 lane bands, no fold", () => {
    const scene = buildGraphScene(multiDelegateExec(), { inputId: INPUT_ID });
    expect(scene.bands.lanes.map((b) => b.kind)).toEqual([
      "delegate",
      "delegate",
    ]);
    expect(scene.bands.lanes.map((b) => b.label)).toEqual([
      "第 1 次委派",
      "第 2 次委派",
    ]);
    expect(scene.fold.folded.size).toBe(0);
    expect(snapshotScene(scene)).toMatchSnapshot();
  });

  it("cold + continuation dual-batch → lanes omit续派, no extra 委派 column", () => {
    const scene = buildGraphScene(multiDelegateWithContinuationExec(), {
      inputId: INPUT_ID,
    });
    expect(scene.bands.lanes.map((b) => b.label)).toEqual([
      "第 1 次委派",
      "第 2 次委派",
    ]);
    expect(scene.bands.lanes.map((b) => b.memberRunIds)).toEqual([
      ["a", "b"],
      ["c", "d"],
    ]);
    expect(scene.bands.lanes.flatMap((b) => b.memberRunIds)).not.toContain(
      "a_v2",
    );
  });

  it("continuation chain → top-level continuation edges, no sub-team box", () => {
    const scene = buildGraphScene(continuationChainExec(), {
      inputId: INPUT_ID,
    });
    expect(scene.subTeams).toHaveLength(0);
    expect(scene.nodeGroup.get("w1_v2") ?? null).toBeNull();
    expect(
      scene.edges
        .filter((e) => e.kind === "continuation")
        .map((e) => `${e.source}->${e.target}`)
        .sort(),
    ).toEqual(["w1->w1_v2", "w1_v2->w1_v3"]);
    // bookend：仅冷开局根接 input；仅链尖汇 CEO（省略中间续的实线 dep）。
    expect(
      scene.edges
        .filter((e) => e.kind === "dep" && e.source === INPUT_ID)
        .map((e) => e.target)
        .sort(),
    ).toEqual(["w1"]);
    expect(
      scene.edges
        .filter((e) => e.kind === "dep" && e.target === "captain")
        .map((e) => e.source)
        .sort(),
    ).toEqual(["w1_v3"]);
    expect(snapshotScene(scene)).toMatchSnapshot();
  });

  it("nested sub-team → nested compounds + nodeGroup attribution", () => {
    // Nested units expand by default on 全屏 + chat embed; pass the same set
    // here to exercise nested compound boxes in the golden.
    const scene = buildGraphScene(nestedSubteamExec(), {
      inputId: INPUT_ID,
      expandedUnits: new Set(["mpm", "lead"]),
    });
    expect(scene.subTeams.map((s) => s.parentId).sort()).toEqual([
      "lead",
      "mpm",
    ]);
    // Each node renders in its innermost box: eng* + lead sit in __group__lead,
    // the mpm parent sits at the top of its own __group__mpm.
    expect(scene.nodeGroup.get("eng1")).toBe("__group__lead");
    expect(scene.nodeGroup.get("lead")).toBe("__group__lead");
    expect(scene.nodeGroup.get("mpm")).toBe("__group__mpm");
    expect(snapshotScene(scene)).toMatchSnapshot();
  });

  it("debate multibeat → beat folds into round hosts, closing stays column", () => {
    const scene = buildGraphScene(debateMultibeatExec(), { inputId: INPUT_ID });
    expect(scene.fold.debateUnits.has("mod")).toBe(true);
    // cross-exam beats fold into their same-round statement host.
    expect(scene.beatFoldsByHost.get("mod_r1_pro")).toEqual(["mod_r1_cx_pro"]);
    expect(scene.beatFoldsByHost.get("mod_r2_pro")).toEqual(["mod_r2_cx_pro"]);
    // beat-hidden cross-exam runs are not graph nodes.
    expect(scene.nodeIds).not.toContain("mod_r1_cx_pro");
    // debate stage bands: 第1轮 / 第2轮 / 结辩.
    expect(scene.bands.debateStages.map((b) => b.label)).toEqual([
      "第 1 轮",
      "第 2 轮",
      "结辩",
    ]);
    expect(snapshotScene(scene)).toMatchSnapshot();
  });

  it("multi-act (≥2) → act bands + acts→units→members hierarchy", () => {
    const scene = buildGraphScene(multiActExec(), { inputId: INPUT_ID });
    expect(scene.acts.map((a) => a.actId)).toEqual(["act-1", "act-2"]);
    expect(scene.acts[0].unitIds).toEqual(["lens_0", "synthesizer"]);
    expect(scene.acts[1].unitIds).toEqual(["mod"]);
    // multi-act ⇒ lane bands are act bands (not waves).
    expect(scene.bands.lanes.map((b) => b.kind)).toEqual(["act", "act"]);
    expect(scene.bands.lanes.map((b) => b.label)).toEqual([
      "多视角调研",
      "辩论对抗 · 经推进卡授权",
    ]);
    // debate moderator stays its own unit, not folded into synthesizer.
    expect(scene.fold.unitOf.get("mod")).toBe("mod");
    expect(scene.fold.unitOf.get("synthesizer")).toBe("synthesizer");
    // 幕派生态（批 R2 幕级 LOD）：memberRunIds 覆盖全幕（含折进拍宿主），unitIds 只有顶层单元。
    expect(scene.acts[0].memberRunIds).toEqual(["lens_0", "synthesizer"]);
    expect(scene.acts[1].memberRunIds).toEqual([
      "mod",
      "mod_r1_pro",
      "mod_r1_con",
    ]);
    expect(scene.acts[0].total).toBe(2);
    expect(scene.acts[1].total).toBe(3);
    // 全 pending → planning；无 running → 自动聚焦首幕。
    expect(scene.acts.map((a) => a.status)).toEqual(["planning", "planning"]);
    expect(scene.activeActId).toBe("act-1");
    expect(snapshotScene(scene)).toMatchSnapshot();
  });

  it("multi-act derived → active act follows running act; status/duration aggregate", () => {
    const acts: ExecutionAct[] = [
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
        anchorRunId: "synthesizer",
        authorizedBy: "stage_card",
      },
    ];
    const scene = buildGraphScene(
      mkExec(
        [
          captain,
          {
            id: "lens_0",
            actId: "act-1",
            status: "completed",
            role: "法律视角",
            durationMs: 800,
          },
          {
            id: "synthesizer",
            dependsOn: ["lens_0"],
            actId: "act-1",
            status: "completed",
            role: "汇总分析师",
            durationMs: 1200,
          },
          {
            id: "mod",
            parentRunId: "synthesizer",
            actId: "act-2",
            status: "running",
            role: "主持人",
          },
          {
            id: "mod_r1_pro",
            parentRunId: "mod",
            actId: "act-2",
            stance: "pro",
            group: "debate:debate",
            status: "running",
            role: "支持方",
          },
          {
            id: "mod_r1_con",
            parentRunId: "mod",
            actId: "act-2",
            stance: "con",
            group: "debate:debate",
            status: "pending",
            role: "反对方",
          },
        ],
        acts,
      ),
      { inputId: INPUT_ID },
    );
    // Act status aggregates: 幕1 全完成 → completed；幕2 有 running → running。
    expect(scene.acts.map((a) => a.status)).toEqual(["completed", "running"]);
    // 执行中自动聚焦：末个含 running 成员的幕。
    expect(scene.activeActId).toBe("act-2");
    // 幕1 用时求和 + 完成计数；参与者角色去重按序（无编造）。
    expect(scene.acts[0].durationMs).toBe(2000);
    expect(scene.acts[0].completed).toBe(2);
    expect(scene.acts[0].roles).toEqual(["法律视角", "汇总分析师"]);
    expect(scene.acts[1].roles).toEqual(["主持人", "支持方", "反对方"]);
  });
});
