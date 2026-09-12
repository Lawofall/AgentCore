import {
  type GraphRunLike,
  buildGraphStructure,
} from "@/components/graph/helpers";
import type { GraphEdge } from "@/stores/graph";
import {
  COMPOUND_LAYER_SPACING,
  EMBED_DEFAULT_COL_WIDTH,
  EMBED_MAX_HEIGHT,
  EMBED_MIN_HEIGHT,
  type SubTeamInput,
  computeLayout,
  estimateBbox,
  fitWidthBox,
  workerGraphShape,
} from "@agentcore/graph-layout";
import { describe, expect, it } from "vitest";

/** Derive compound sub-teams from delegate edges (mirrors buildGraphStructure). */
function subTeamsFromEdges(edges: GraphEdge[]): SubTeamInput[] {
  const subTeamMap = new Map<string, string[]>();
  for (const e of edges) {
    if (e.kind !== "delegate") continue;
    const arr = subTeamMap.get(e.source) ?? [];
    arr.push(e.target);
    subTeamMap.set(e.source, arr);
  }
  return [...subTeamMap.entries()].map(([parentId, memberIds]) => ({
    parentId,
    memberIds,
    groupId: `__group__${parentId}`,
  }));
}

async function layout(
  ids: string[],
  edges: GraphEdge[],
  layoutKind: "tree" | "leftright" = "leftright",
  bookends: { source?: string; sink?: string } = {},
) {
  return computeLayout(
    ids,
    edges,
    layoutKind,
    bookends,
    subTeamsFromEdges(edges),
  );
}

/**
 * 协作图布局后处理不变量（端点钉层 + ELK compound 子队，见 elk-layout.ts / 前端UX设计 §五）。
 *
 * 用真实 `computeLayout`（含 ELK + layerConstraint + compound 子队 +
 * centerLoneEndpoints）断言几何不变量。含 delegate 的用例以 compound 容器为单元：
 *   1. 末层钉层——CEO 汇聚点恒在最右（最大主轴坐标）。
 *   2. 同 compound 内成员两两不重叠。
 *   3. 多支子树（compound 或顶层节点）互不重叠。
 */
// 镜像 elk-layout 内部常量（NODE_WIDTH 未导出，NODE_HEIGHT 已导出但此处一并固定）。
const NW = 210;
const NH = 110;

const e = (
  source: string,
  target: string,
  kind: "dep" | "delegate" | "continuation" = "dep",
): GraphEdge => ({ id: `${source}->${target}`, source, target, kind });

/** 两节点盒（NW×NH）是否相交。 */
const overlaps = (
  a: { x: number; y: number },
  b: { x: number; y: number },
): boolean =>
  a.x < b.x + NW && a.x + NW > b.x && a.y < b.y + NH && a.y + NH > b.y;

/** 任意两两不重叠。 */
const noneOverlap = (
  pos: Record<string, { x: number; y: number }>,
  ids: string[],
): string[] => {
  const hits: string[] = [];
  for (let i = 0; i < ids.length; i++) {
    for (let j = i + 1; j < ids.length; j++) {
      if (overlaps(pos[ids[i]], pos[ids[j]])) hits.push(`${ids[i]}×${ids[j]}`);
    }
  }
  return hits;
};

describe("computeLayout · 嵌套委派布局不变量（leftright）", () => {
  it("2 级嵌套：compound 子队不重叠、汇聚点钉末层", async () => {
    const ids = ["__input__", "mpm", "lead", "eng1", "eng2", "mcap"];
    const edges: GraphEdge[] = [
      e("__input__", "mpm"),
      e("mpm", "lead", "delegate"),
      e("lead", "eng1", "delegate"),
      e("lead", "eng2", "delegate"),
      e("mpm", "mcap"),
    ];
    const { positions, groups } = await layout(ids, edges, "leftright", {
      source: "__input__",
      sink: "mcap",
    });

    const maxX = Math.max(...ids.map((id) => positions[id].x));
    expect(positions.mcap.x).toBe(maxX);
    expect(groups.length).toBeGreaterThanOrEqual(2);
    expect(noneOverlap(positions, ["lead", "eng1", "eng2"])).toEqual([]);
    expect(
      noneOverlap(
        positions,
        ids.filter((id) => !id.startsWith("__")),
      ),
    ).toEqual([]);

    // 外层 __group__mpm 须包住内层组与嵌套叶子（eng*），避免右/底 chrome 缺口。
    const outer = groups.find((gr) => gr.groupId === "__group__mpm");
    const inner = groups.find((gr) => gr.groupId === "__group__lead");
    expect(outer).toBeDefined();
    expect(inner).toBeDefined();
    if (!outer || !inner) return;
    expect(inner.x).toBeGreaterThanOrEqual(outer.x - 0.01);
    expect(inner.y).toBeGreaterThanOrEqual(outer.y - 0.01);
    expect(inner.x + inner.width).toBeLessThanOrEqual(
      outer.x + outer.width + 0.01,
    );
    expect(inner.y + inner.height).toBeLessThanOrEqual(
      outer.y + outer.height + 0.01,
    );
    for (const id of ["mpm", "lead", "eng1", "eng2"]) {
      expect(positions[id].x).toBeGreaterThanOrEqual(outer.x - 0.01);
      expect(positions[id].y).toBeGreaterThanOrEqual(outer.y - 0.01);
      expect(positions[id].x + NW).toBeLessThanOrEqual(
        outer.x + outer.width + 0.01,
      );
      expect(positions[id].y + NH).toBeLessThanOrEqual(
        outer.y + outer.height + 0.01,
      );
    }
  });

  it("同层双父各带子团队：两支 compound 互不重叠、汇聚点钉末层", async () => {
    const ids = ["__input__", "be", "fe", "be1", "be2", "fe1", "fe2", "dcap"];
    const edges: GraphEdge[] = [
      e("__input__", "be"),
      e("__input__", "fe"),
      e("be", "be1", "delegate"),
      e("be", "be2", "delegate"),
      e("fe", "fe1", "delegate"),
      e("fe", "fe2", "delegate"),
      e("be", "dcap"),
      e("fe", "dcap"),
    ];
    const { positions, groups } = await layout(ids, edges, "leftright", {
      source: "__input__",
      sink: "dcap",
    });

    expect(groups.length).toBe(2);
    expect(
      noneOverlap(positions, ["be", "fe", "be1", "be2", "fe1", "fe2"]),
    ).toEqual([]);

    const maxX = Math.max(...ids.map((id) => positions[id].x));
    expect(positions.dcap.x).toBe(maxX);
  });

  it("大扇出 1→8→7→1 DAG + delegate 子队：同层节点无重叠", async () => {
    // 模拟 3 波复杂协作：wave1 四父各带 2 子队（8 subs），wave2 七普通 worker，
    // 子队 compound 与同层 dep 节点混排——断言 ELK 直接把它们排开、无重叠。
    const parents = ["p0", "p1", "p2", "p3"];
    const subs = Array.from({ length: 8 }, (_, i) => `sub_${i}`);
    const w2 = Array.from({ length: 7 }, (_, i) => `w2_${i}`);
    const ids = ["__input__", ...parents, ...subs, ...w2, "cap"];
    const edges: GraphEdge[] = [
      ...parents.map((p) => e("__input__", p)),
      e("p0", "sub_0", "delegate"),
      e("p0", "sub_1", "delegate"),
      e("p1", "sub_2", "delegate"),
      e("p1", "sub_3", "delegate"),
      e("p2", "sub_4", "delegate"),
      e("p2", "sub_5", "delegate"),
      e("p3", "sub_6", "delegate"),
      e("p3", "sub_7", "delegate"),
      ...w2.flatMap((w, i) => [
        e(parents[i % parents.length], w),
        ...(i + 1 < w2.length ? [e(parents[(i + 1) % parents.length], w)] : []),
      ]),
      ...parents.map((p) => e(p, "cap")),
      ...w2.map((w) => e(w, "cap")),
    ];
    const workers = [...parents, ...subs, ...w2];
    const { positions } = await layout(ids, edges, "leftright", {
      source: "__input__",
      sink: "cap",
    });

    expect(noneOverlap(positions, workers)).toEqual([]);
    const maxX = Math.max(...ids.map((id) => positions[id].x));
    expect(positions.cap.x).toBe(maxX);
  });

  it("扁平并行（无委派）：端点钉首/末层、并行列无重叠", async () => {
    const ids = ["__input__", "w1", "w2", "w3", "cap"];
    const edges: GraphEdge[] = [
      e("__input__", "w1"),
      e("__input__", "w2"),
      e("__input__", "w3"),
      e("w1", "cap"),
      e("w2", "cap"),
      e("w3", "cap"),
    ];
    const { positions } = await layout(ids, edges, "leftright", {
      source: "__input__",
      sink: "cap",
    });

    expect(noneOverlap(positions, ["w1", "w2", "w3"])).toEqual([]);
    expect(positions.__input__.x).toBeLessThan(positions.w1.x);
    expect(positions.cap.x).toBeGreaterThan(positions.w1.x);
  });

  it("3 父同波次各带子队：compound 互不重叠、汇聚点钉末层", async () => {
    const ids = [
      "__input__",
      "p1",
      "p2",
      "p3",
      "a1",
      "a2",
      "b1",
      "b2",
      "c1",
      "c2",
      "cap",
    ];
    const edges: GraphEdge[] = [
      e("__input__", "p1"),
      e("__input__", "p2"),
      e("__input__", "p3"),
      e("p1", "a1", "delegate"),
      e("p1", "a2", "delegate"),
      e("p2", "b1", "delegate"),
      e("p2", "b2", "delegate"),
      e("p3", "c1", "delegate"),
      e("p3", "c2", "delegate"),
      e("p1", "cap"),
      e("p2", "cap"),
      e("p3", "cap"),
    ];
    const { positions, groups } = await layout(ids, edges, "leftright", {
      source: "__input__",
      sink: "cap",
    });

    expect(groups.length).toBe(3);
    expect(
      noneOverlap(positions, [
        "p1",
        "p2",
        "p3",
        "a1",
        "a2",
        "b1",
        "b2",
        "c1",
        "c2",
      ]),
    ).toEqual([]);
    const maxX = Math.max(...ids.map((id) => positions[id].x));
    expect(positions.cap.x).toBe(maxX);
  });

  it("树形(DOWN) + 委派：compound 子队不重叠、汇聚点钉末层", async () => {
    const ids = ["__input__", "tpm", "teng1", "teng2", "tcap"];
    const edges: GraphEdge[] = [
      e("__input__", "tpm"),
      e("tpm", "teng1", "delegate"),
      e("tpm", "teng2", "delegate"),
      e("tpm", "tcap"),
    ];
    const { positions, groups } = await layout(ids, edges, "tree", {
      source: "__input__",
      sink: "tcap",
    });

    expect(groups.length).toBe(1);
    const maxY = Math.max(...ids.map((id) => positions[id].y));
    expect(positions.tcap.y).toBe(maxY);
    expect(noneOverlap(positions, ["tpm", "teng1", "teng2"])).toEqual([]);
  });

  it("B 型回归：深波次普通 worker 与 compound 子队不重叠", async () => {
    // input→p1⇢{s1,s2}→cap、input→p2⇢{t1,t2}→cap、input→m→n→cap（n 落到子队所在层）。
    const ids = [
      "__input__",
      "p1",
      "p2",
      "s1",
      "s2",
      "t1",
      "t2",
      "m",
      "n",
      "cap",
    ];
    const edges: GraphEdge[] = [
      e("__input__", "p1"),
      e("p1", "s1", "delegate"),
      e("p1", "s2", "delegate"),
      e("p1", "cap"),
      e("__input__", "p2"),
      e("p2", "t1", "delegate"),
      e("p2", "t2", "delegate"),
      e("p2", "cap"),
      e("__input__", "m"),
      e("m", "n"),
      e("n", "cap"),
    ];
    const { positions } = await layout(ids, edges, "leftright", {
      source: "__input__",
      sink: "cap",
    });

    expect(
      noneOverlap(positions, ["p1", "p2", "s1", "s2", "t1", "t2", "m", "n"]),
    ).toEqual([]);
    const maxX = Math.max(...ids.map((id) => positions[id].x));
    expect(positions.cap.x).toBe(maxX);
  });

  // 圆桌逐轮（主持人 ⇢ 三视角 ⇢ 各自修订 v2）：修订节点必须与其源同一交叉轴车道（修订边笔直），
  // 守住「第三波漂移」回归——修订须与源同处一个 compound，否则曾被 ELK 甩到框外的独立车道。
  it("圆桌逐轮·无汇聚点：三方修订各与源同车道、互不重叠", async () => {
    const ids = ["mod", "s_a", "s_b", "s_c", "s_a2", "s_b2", "s_c2"];
    const edges: GraphEdge[] = [
      e("mod", "s_a", "delegate"),
      e("mod", "s_b", "delegate"),
      e("mod", "s_c", "delegate"),
      e("s_a", "s_a2", "continuation"),
      e("s_b", "s_b2", "continuation"),
      e("s_c", "s_c2", "continuation"),
    ];
    const { positions } = await layout(ids, edges, "leftright", {
      source: "__input__",
    });

    // 每个修订与其源同 y（同车道）——核心不变量，杜绝第三波漂移。
    for (const [src, rev] of [
      ["s_a", "s_a2"],
      ["s_b", "s_b2"],
      ["s_c", "s_c2"],
    ]) {
      expect(positions[rev].y).toBeCloseTo(positions[src].y, 5);
    }
    // 全员两两不重叠。
    expect(noneOverlap(positions, ids)).toEqual([]);
    // 主持人领先圆桌（主轴更靠前）：leftright 下 mod.x 小于三视角。交叉轴上 ELK(BK)
    // 把 mod 居中于扇面而非钉顶——等效可读布局，故只守「源领先目标」这一真不变量。
    const subXs = ["s_a", "s_b", "s_c"].map((id) => positions[id].x);
    expect(positions.mod.x).toBeLessThan(Math.min(...subXs));
  });

  it("圆桌逐轮·带汇聚点：修订与源同车道、汇聚点钉末层", async () => {
    const ids = [
      "__input__",
      "mod",
      "s_a",
      "s_b",
      "s_c",
      "s_a2",
      "s_b2",
      "s_c2",
      "cap",
    ];
    const edges: GraphEdge[] = [
      e("__input__", "mod"),
      e("mod", "s_a", "delegate"),
      e("mod", "s_b", "delegate"),
      e("mod", "s_c", "delegate"),
      e("s_a", "s_a2", "continuation"),
      e("s_b", "s_b2", "continuation"),
      e("s_c", "s_c2", "continuation"),
      e("mod", "cap"),
    ];
    const { positions } = await layout(ids, edges, "leftright", {
      source: "__input__",
      sink: "cap",
    });

    // 修订与源同车道（compound 内修订边把 vN 排在源同一行）。
    for (const [src, rev] of [
      ["s_a", "s_a2"],
      ["s_b", "s_b2"],
      ["s_c", "s_c2"],
    ]) {
      expect(positions[rev].y).toBeCloseTo(positions[src].y, 5);
    }
    // 汇聚点钉末层。
    const maxX = Math.max(...ids.map((id) => positions[id].x));
    expect(positions.cap.x).toBe(maxX);
    expect(
      noneOverlap(positions, ["s_a", "s_b", "s_c", "s_a2", "s_b2", "s_c2"]),
    ).toEqual([]);
  });

  // 修订轮必须落在子队 box 内、且紧贴源列成 grid（参与者=行, 轮次=列）——回归用户报的
  // 双 bug：修订逃逸到框外 + 成员回列后修订留在 ELK 旧坐标造成的 phantom gap。
  it("圆桌逐轮：子队框包住所有修订轮、轮次紧贴源列（无逃逸 + 无 phantom gap）", async () => {
    const ids = ["mod", "s_a", "s_b", "s_c", "s_a2", "s_b2", "s_c2"];
    const edges: GraphEdge[] = [
      e("mod", "s_a", "delegate"),
      e("mod", "s_b", "delegate"),
      e("mod", "s_c", "delegate"),
      e("s_a", "s_a2", "continuation"),
      e("s_b", "s_b2", "continuation"),
      e("s_c", "s_c2", "continuation"),
    ];
    const { positions, groups } = await layout(ids, edges, "leftright", {
      source: "__input__",
    });

    // 子队框（__group__mod）包住每一个修订轮（含边距，不逃逸到框外）。
    const g = groups.find((gr) => gr.parentId === "mod");
    expect(g).toBeDefined();
    if (!g) return;
    const within = (id: string): boolean =>
      positions[id].x >= g.x - 0.01 &&
      positions[id].x + NW <= g.x + g.width + 0.01 &&
      positions[id].y >= g.y - 0.01 &&
      positions[id].y + NH <= g.y + g.height + 0.01;
    for (const id of ids) expect(within(id)).toBe(true);

    // 每个修订紧贴其源右侧一列（= NW + 子队内层间距）——phantom gap 会让这个间距翻倍。
    for (const [src, rev] of [
      ["s_a", "s_a2"],
      ["s_b", "s_b2"],
      ["s_c", "s_c2"],
    ]) {
      expect(positions[rev].x - positions[src].x).toBeCloseTo(
        NW + COMPOUND_LAYER_SPACING,
        3,
      );
    }
  });
});

describe("computeLayout · 树形分叉对称", () => {
  it("1→2 双父 + 各自子队：两支镜像等距、汇聚点钉末层", async () => {
    const ids = [
      "__input__",
      "decide",
      "pd",
      "arch",
      "ix",
      "vd",
      "be",
      "dm",
      "cap",
    ];
    const edges: GraphEdge[] = [
      e("__input__", "decide"),
      e("decide", "pd"),
      e("decide", "arch"),
      e("pd", "ix", "delegate"),
      e("pd", "vd", "delegate"),
      e("arch", "be", "delegate"),
      e("arch", "dm", "delegate"),
      e("pd", "cap"),
      e("arch", "cap"),
    ];
    const { positions, groups } = await layout(ids, edges, "tree", {
      source: "__input__",
      sink: "cap",
    });

    const cx = (id: string) => positions[id].x + NW / 2;
    const decideC = cx("decide");
    const pdDist = decideC - cx("pd");
    const archDist = cx("arch") - decideC;
    expect(pdDist).toBeCloseTo(archDist, 0);
    expect(pdDist).toBeGreaterThan(NW * 0.5);
    expect(groups.length).toBe(2);
    expect(
      noneOverlap(positions, ["pd", "arch", "ix", "vd", "be", "dm"]),
    ).toEqual([]);
    const maxY = Math.max(...ids.map((id) => positions[id].y));
    expect(positions.cap.y).toBe(maxY);
  });
});

// 多轮辩论版本链（原始 + 修订 v2…v5 全部 revisionOf==原始 的「星型」数据）必须铺成一条可见的链，
// 而非全部堆叠在原始的唯一后继槽里——回归「5 轮辩论协作图只显 2 个版本」bug（修订 v3/v4 被 v5 压盖）。
describe("buildGraphStructure · 多修订版本链（辩论逐轮）", () => {
  const side = (
    prefix: string,
    stance: "pro" | "con",
    rounds: number,
  ): GraphRunLike[] => {
    const original: GraphRunLike = {
      id: `mod_r1_${prefix}`,
      dependsOn: [],
      parentRunId: "mod",
      continuationIndex: 0,
      continuesRunId: null,
      stance,
    };
    // 后续每一轮都是首轮的续写 revision——真实投影里 revisionOf 恒指向【原始】(r1)，形成星型。
    // 乙 wire 携 round/stance 后每个修订也继承本方 stance（fold 投上去），故这里也带 stance——
    // 它改变 nodeId 的 stance 排序（同方版本聚拢），链式修订边仍须成立、布局仍不叠。
    const revs: GraphRunLike[] = [];
    for (let r = 2; r <= rounds; r++) {
      revs.push({
        id: `mod_r${r}_${prefix}`,
        dependsOn: [],
        parentRunId: original.id,
        continuationIndex: r - 1,
        continuesRunId: original.id,
        stance,
      });
    }
    return [original, ...revs];
  };

  const debateRuns = (rounds: number): GraphRunLike[] => [
    { id: "mod", dependsOn: [], parentRunId: null, kind: "agent" },
    ...side("pro", "pro", rounds),
    ...side("con", "con", rounds),
  ];

  it("星型 revisionOf 被铺成链式修订边（原始→v2→v3→…），不是从原始发散的星", () => {
    const { rawEdges } = buildGraphStructure(
      debateRuns(5),
      "__input__",
      new Set(["mod"]),
    );
    const revEdges = rawEdges
      .filter((e) => e.kind === "continuation")
      .map((e) => `${e.source}->${e.target}`)
      .sort();
    expect(revEdges).toEqual(
      [
        "mod_r1_pro->mod_r2_pro",
        "mod_r2_pro->mod_r3_pro",
        "mod_r3_pro->mod_r4_pro",
        "mod_r4_pro->mod_r5_pro",
        "mod_r1_con->mod_r2_con",
        "mod_r2_con->mod_r3_con",
        "mod_r3_con->mod_r4_con",
        "mod_r4_con->mod_r5_con",
      ].sort(),
    );
    // 没有从原始直接连到 v3/v4/v5 的星型边（那正是导致堆叠的形状）。
    expect(revEdges).not.toContain("mod_r1_pro->mod_r3_pro");
    expect(revEdges).not.toContain("mod_r1_pro->mod_r5_pro");
  });

  it("布局后 5 个版本全部落在各自图元、两两不重叠（不再折叠成 2 个）", async () => {
    const expanded = new Set(["mod"]);
    const { nodeIds, rawEdges, subTeams } = buildGraphStructure(
      debateRuns(5),
      "__input__",
      expanded,
    );
    const { positions } = await computeLayout(
      nodeIds,
      rawEdges,
      "leftright",
      { source: "__input__" },
      subTeams,
    );
    const proVersions = [
      "mod_r1_pro",
      "mod_r2_pro",
      "mod_r3_pro",
      "mod_r4_pro",
      "mod_r5_pro",
    ];
    const conVersions = [
      "mod_r1_con",
      "mod_r2_con",
      "mod_r3_con",
      "mod_r4_con",
      "mod_r5_con",
    ];
    // 每个版本都被 ELK 放置（有坐标）。
    for (const id of [...proVersions, ...conVersions]) {
      expect(positions[id]).toBeDefined();
    }
    // 同一辩手的 5 个版本两两不重叠（旧星型下 v2…v5 会叠在同一坐标 → 只看得到 v5）。
    expect(noneOverlap(positions, proVersions)).toEqual([]);
    expect(noneOverlap(positions, conVersions)).toEqual([]);
  });

  it("辩论 compound 原点钉在 padding、bbox 无虚高死区", async () => {
    const side = (prefix: string, stance: "pro" | "con"): GraphRunLike[] => {
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
          id: `mod_closing_${prefix}`,
          dependsOn: [],
          parentRunId: original.id,
          continuationIndex: 2,
          continuesRunId: original.id,
          stance,
          group: "debate:debate",
          round: 1,
          receivedContext: [{ channel: "closing" }],
        },
      ];
    };
    const runs: GraphRunLike[] = [
      { id: "captain", dependsOn: [], kind: "captain" },
      { id: "mod", dependsOn: [], parentRunId: null, kind: "agent" },
      ...side("pro", "pro"),
      ...side("con", "con"),
    ];
    const { nodeIds, rawEdges, subTeams } = buildGraphStructure(
      runs,
      "__input__",
    );
    // 质询折进轮节点：每方仅陈词 + 结辩。
    expect(nodeIds).not.toContain("mod_r1_cx_pro");
    expect(nodeIds).not.toContain("mod_r1_cx_con");
    const { positions, width, height, groups } = await computeLayout(
      nodeIds,
      rawEdges,
      "leftright",
      { source: "__input__", sink: "captain" },
      subTeams,
    );
    const ys = Object.values(positions).map((p) => p.y);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    // Origin pinned to padding (24); no multi-hundred-px dead band above content.
    expect(minY).toBeGreaterThanOrEqual(24);
    expect(minY).toBeLessThan(80);
    // Bbox height tracks content span, not ELK's inflated root stamp.
    expect(height).toBeLessThan(maxY - minY + 300);
    expect(width).toBeGreaterThan(0);
    const g = groups.find((x) => x.groupId === "__group__mod");
    expect(g).toBeDefined();
    if (g == null) throw new Error("expected debate group");
    expect(g.y).toBeGreaterThanOrEqual(24);
  });

  it("workerGraphShape 辩论网格：compoundLanes=辩手数，首帧 fit 高度贴近真实布局", async () => {
    const runs: GraphRunLike[] = [
      { id: "captain", dependsOn: [], kind: "captain" },
      {
        id: "mod",
        dependsOn: [],
        parentRunId: null,
        kind: "agent",
        group: "debate:debate",
      },
      {
        id: "mod_r1_pro",
        dependsOn: [],
        parentRunId: "mod",
        continuationIndex: 0,
        stance: "pro",
        group: "debate:debate",
      },
      {
        id: "mod_r1_con",
        dependsOn: [],
        parentRunId: "mod",
        continuationIndex: 0,
        stance: "con",
        group: "debate:debate",
      },
    ];
    const shape = workerGraphShape(runs);
    expect(shape.compoundLanes).toBe(2);
    expect(shape.parallelism).toBe(1);
    expect(shape.depth).toBe(4);

    const est = estimateBbox(shape, "leftright");
    const estFit = fitWidthBox(est.width, est.height, EMBED_DEFAULT_COL_WIDTH);
    const { nodeIds, rawEdges, subTeams } = buildGraphStructure(
      runs,
      "__input__",
    );
    const layout = await computeLayout(
      nodeIds,
      rawEdges,
      "leftright",
      { source: "__input__", sink: "captain" },
      subTeams,
    );
    const realFit = fitWidthBox(
      layout.width,
      layout.height,
      EMBED_DEFAULT_COL_WIDTH,
    );
    // Old collapse-to-parallelism=1 path clamped at EMBED_MIN (180) while real
    // sat ~225 — a ~45px first-paint jump. Compound-lane estimate stays within
    // a small band of the measured fit height.
    expect(Math.abs(estFit.height - realFit.height)).toBeLessThan(20);
    expect(estFit.height).toBeGreaterThan(EMBED_MIN_HEIGHT);
  });

  it("workerGraphShape 多轮：深度按可见轮列（质询不计），不把主持人算进车道", () => {
    const runs: GraphRunLike[] = [
      { id: "captain", dependsOn: [], kind: "captain" },
      {
        id: "mod",
        dependsOn: [],
        kind: "agent",
        group: "debate:debate",
      },
      {
        id: "mod_r1_pro",
        dependsOn: [],
        parentRunId: "mod",
        continuationIndex: 0,
        stance: "pro",
        group: "debate:debate",
        round: 1,
      },
      {
        id: "mod_r1_cx_pro",
        dependsOn: [],
        parentRunId: "mod_r1_pro",
        continuationIndex: 1,
        continuesRunId: "mod_r1_pro",
        stance: "pro",
        group: "debate:debate",
        round: 1,
        receivedContext: [{ channel: "cross_exam" }],
      },
      {
        id: "mod_r2_pro",
        dependsOn: [],
        parentRunId: "mod_r1_pro",
        continuationIndex: 2,
        continuesRunId: "mod_r1_pro",
        stance: "pro",
        group: "debate:debate",
        round: 2,
      },
      {
        id: "mod_r1_con",
        dependsOn: [],
        parentRunId: "mod",
        continuationIndex: 0,
        stance: "con",
        group: "debate:debate",
        round: 1,
      },
      {
        id: "mod_r1_cx_con",
        dependsOn: [],
        parentRunId: "mod_r1_con",
        continuationIndex: 1,
        continuesRunId: "mod_r1_con",
        stance: "con",
        group: "debate:debate",
        round: 1,
        receivedContext: [{ channel: "cross_exam" }],
      },
      {
        id: "mod_r2_con",
        dependsOn: [],
        parentRunId: "mod_r1_con",
        continuationIndex: 2,
        continuesRunId: "mod_r1_con",
        stance: "con",
        group: "debate:debate",
        round: 2,
      },
    ];
    const shape = workerGraphShape(runs);
    expect(shape.compoundLanes).toBe(2);
    // input + mod + 2 轮列（质询折进）+ captain
    expect(shape.depth).toBe(5);
  });
});

describe("fitWidthBox (embed height cap)", () => {
  it("shrinks zoom when content is taller than EMBED_MAX_HEIGHT (no clip)", () => {
    // 4 路并行 leftright 典型 footprint：宽约一列节点、高超 520。
    const bboxW = 500;
    const bboxH = EMBED_MAX_HEIGHT + 120;
    const fit = fitWidthBox(bboxW, bboxH, EMBED_DEFAULT_COL_WIDTH);
    expect(fit.zoom).toBeLessThan(1);
    expect(fit.height).toBeLessThanOrEqual(EMBED_MAX_HEIGHT);
    expect(fit.renderedHeight).toBeLessThanOrEqual(EMBED_MAX_HEIGHT + 1);
    expect(fit.overflowing).toBe(false);
  });

  it("keeps zoom=1 when both axes fit", () => {
    const fit = fitWidthBox(400, 300, EMBED_DEFAULT_COL_WIDTH);
    expect(fit.zoom).toBe(1);
    expect(fit.height).toBe(300);
    expect(fit.overflowing).toBe(false);
  });
});

describe("computeLayout · 实测高度 > NODE_HEIGHT 同列不重叠", () => {
  const overlapsSized = (
    a: { x: number; y: number },
    ah: number,
    b: { x: number; y: number },
    bh: number,
  ): boolean =>
    a.x < b.x + NW && a.x + NW > b.x && a.y < b.y + bh && a.y + ah > b.y;

  it("并行 be/fe 用 180/200 高布局时包围盒不相交且 gap ≥ nodeSpacing", async () => {
    const ids = ["__input__", "be", "fe", "cap"];
    const edges: GraphEdge[] = [
      e("__input__", "be"),
      e("__input__", "fe"),
      e("be", "cap"),
      e("fe", "cap"),
    ];
    const tall = {
      __input__: { width: NW, height: NH },
      be: { width: NW, height: 180 },
      fe: { width: NW, height: 200 },
      cap: { width: NW, height: NH },
    };
    const spacing = 56;
    const { positions } = await computeLayout(
      ids,
      edges,
      "leftright",
      { source: "__input__", sink: "cap" },
      [],
      spacing,
      tall,
    );

    expect(overlapsSized(positions.be, 180, positions.fe, 200)).toBe(false);

    const [upper, lower] =
      positions.be.y <= positions.fe.y
        ? [
            { p: positions.be, h: 180 },
            { p: positions.fe, h: 200 },
          ]
        : [
            { p: positions.fe, h: 200 },
            { p: positions.be, h: 180 },
          ];
    const gap = lower.p.y - (upper.p.y + upper.h);
    expect(gap).toBeGreaterThanOrEqual(spacing - 1);
  });

  it("冷启动固定 110 下同图无重叠（回归：加高 path 不破坏默认）", async () => {
    const ids = ["__input__", "be", "fe", "cap"];
    const edges: GraphEdge[] = [
      e("__input__", "be"),
      e("__input__", "fe"),
      e("be", "cap"),
      e("fe", "cap"),
    ];
    const { positions } = await layout(ids, edges, "leftright", {
      source: "__input__",
      sink: "cap",
    });
    expect(noneOverlap(positions, ["be", "fe", "cap"])).toEqual([]);
  });
});
