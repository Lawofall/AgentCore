import {
  artifactFromRun,
  buildCrystallizedElements,
} from "@/services/boardCrystallize";
import {
  type OverlayAnchor,
  buildProgressOverlay,
} from "@/services/boardProgress";
import type {
  AgentState,
  Execution,
  RunNode,
  RunStatus,
} from "@/stores/execution";
import { SCENE_SCHEMA_VERSION, type SceneElement } from "@/whiteboard";
import { layoutDagre } from "@/whiteboard/layoutDagre";

/**
 * Offline preview scene vectors for the self-built whiteboard canvas (AI协作白板.md §六).
 *
 * The chat preview (`#/preview`) replays backend SSE golden vectors; the whiteboard is a
 * separate canvas surface whose "vector" is a SCENE (a list of {@link SceneElement}) rendered by
 * the real {@link WhiteboardCanvas}. Each scene here is either authored directly or DERIVED from
 * the real M3 projectors (`buildProgressOverlay` / `buildCrystallizedElements`) and the dagre
 * auto-layout, so previewing a scene exercises the same pure logic production uses — no drift.
 *
 * These back the offline preview route (`#/preview/whiteboard`) and its screenshot smoke gate
 * (`scripts/shoot-whiteboard.mjs`, companion to `shoot-manual.mjs`). Scenes serialize through the
 * board scene format (round-tripped in the unit test), so they are exportable board vectors.
 */

export interface WhiteboardScene {
  id: string;
  description: string;
  elements: SceneElement[];
  /** Element ids to preselect (rotation handle / selection bar states). */
  selectedIds?: string[];
}

function mkAgent(id: string, role: string): AgentState {
  return {
    id,
    role,
    thinking: true,
    status: "idle",
    currentRunId: null,
    outputChunks: [],
    reasoningChunks: [],
    toolCalls: [],
    toolProgress: null,
    toolExecutionLive: null,
  };
}

function mkRun(
  id: string,
  agentId: string,
  status: RunStatus,
  over: Partial<RunNode> = {},
): RunNode {
  return {
    id,
    agentId,
    task: `task ${id}`,
    status,
    dependsOn: [],
    outputSummary: null,
    outputFiles: [],
    debrief: null,
    durationMs: null,
    startedAt: null,
    error: null,
    parentRunId: null,
    kind: "agent",
    role: null,
    model: null,
    usage: null,
    cost: null,
    stance: null,
    group: null,
    round: 0,
    continuesRunId: null,
    continuationIndex: 0,
    revised: null,
    replacesRunId: null,
    checkpoint: null,
    receivedContext: [],
    escalations: [],
    process: [],
    ...over,
    sideKey: over.sideKey ?? null,
  };
}

function mkExec(over: Partial<Execution>): Execution {
  return {
    id: "exec-preview",
    planType: "multi_agent",
    taskSummary: "照白板实现",
    status: "running",
    agents: [],
    runs: [],
    progress: { completed: 0, total: 0 },
    batches: [],
    debate: null,
    debateRounds: [],
    crossExamEnabled: false,
    debateOpening: null,
    debatePretrial: null,
    ...over,
    acts: over.acts ?? [],
  };
}

/** A visible stand-in for the launching brief (the user's selection / frame) so the projected
 * team clusters + provenance arrows have a readable target on the canvas. */
function briefFrame(anchor: OverlayAnchor, label: string): SceneElement {
  return {
    id: "brief-frame",
    type: "frame",
    x: anchor.x,
    y: anchor.y,
    width: anchor.width,
    height: anchor.height,
    text: label,
    schemaVersion: SCENE_SCHEMA_VERSION,
  };
}

const ANCHOR: OverlayAnchor = { x: 40, y: 60, width: 220, height: 150 };

// ── M3 进度贴源 (live overlay): a mid-flight team, projected by the real overlay builder. ──
function progressOverlayScene(): WhiteboardScene {
  const exec = mkExec({
    status: "running",
    agents: [
      mkAgent("a1", "研究员"),
      mkAgent("a2", "工程师"),
      mkAgent("a3", "文案"),
    ],
    runs: [
      mkRun("r1", "a1", "completed"),
      mkRun("r2", "a2", "running"),
      mkRun("r3", "a3", "pending"),
    ],
  });
  return {
    id: "board_progress_overlay",
    description:
      "M3 进度贴源：活 run 树投影成瞬时浮层（团队状态卡 + 指回 brief 的箭头）",
    elements: [
      briefFrame(ANCHOR, "需求 brief（选区）"),
      ...buildProgressOverlay(exec, ANCHOR),
    ],
  };
}

// ── M3 crystallize: a finished team固化成 persistent nodes + product cards. ──
function crystallizedTeamScene(): WhiteboardScene {
  const exec = mkExec({
    status: "completed",
    agents: [mkAgent("a1", "研究员"), mkAgent("a2", "工程师")],
    runs: [
      mkRun("r1", "a1", "completed", {
        outputSummary: "竞品调研完成：三家定价拆解",
      }),
      mkRun("r2", "a2", "completed", {
        outputSummary: "原型骨架已搭：登录 + 仪表盘",
      }),
    ],
  });
  return {
    id: "board_crystallized_team",
    description:
      "M3 产物回贴：终态团队 crystallize 成持久 agentNode + 文本产物卡 + 溯源连线",
    elements: [
      briefFrame(ANCHOR, "需求 brief（选区）"),
      ...buildCrystallizedElements(exec, ANCHOR, new Set()),
    ],
  };
}

// ── WB-003 文件产物卡: a completed run that wrote a workspace file → `file` artifactCard. ──
function fileArtifactScene(): WhiteboardScene {
  const exec = mkExec({
    status: "completed",
    agents: [mkAgent("a1", "工程师")],
    runs: [
      mkRun("r1", "a1", "completed", {
        outputSummary: "报告已写入工作区，含结论与附录",
        outputFiles: ["AgentCore/文档/research/竞品分析.md"],
      }),
    ],
  });
  const cards = buildCrystallizedElements(exec, ANCHOR, new Set());
  const artId = cards.find((e) => e.type === "artifactCard")?.id;
  return {
    id: "board_file_artifact",
    description:
      "WB-003 文件产物卡：artifactKind=file，卡片末行显示「↗ 路径」，双击可开工作区预览",
    elements: [briefFrame(ANCHOR, "需求 brief（选区）"), ...cards],
    selectedIds: artId ? [artId] : undefined,
  };
}

// ── M3 迭代贴旁留旧: v1 kept, v2 crystallized below it (real projector, two turns). ──
function iterateVersionsScene(): WhiteboardScene {
  const v1Exec = mkExec({
    id: "exec-v1",
    status: "completed",
    agents: [mkAgent("a1", "文案")],
    runs: [
      mkRun("r1", "a1", "completed", { outputSummary: "初稿：一句话价值主张" }),
    ],
  });
  const v2Exec = mkExec({
    id: "exec-v2",
    status: "completed",
    agents: [mkAgent("a2", "文案")],
    runs: [
      mkRun("r2", "a2", "completed", {
        outputSummary: "二稿：按「更口语、加数字」批注重写",
      }),
    ],
  });
  const v1 = buildCrystallizedElements(v1Exec, ANCHOR, new Set());
  // The next turn keeps the same brief anchor but drops the new cluster a row below so both
  // versions read as「旧版留痕 + 新版贴旁」on one canvas.
  const v2Anchor: OverlayAnchor = { ...ANCHOR, y: ANCHOR.y + 200 };
  const v2 = buildCrystallizedElements(v2Exec, v2Anchor, new Set(["r1"]));
  return {
    id: "board_iterate_versions",
    description:
      "M3 贴源迭代：上一版保留、新版按新 runId 贴旁（两轮 crystallize 叠加）",
    elements: [briefFrame(ANCHOR, "需求 brief（选区）"), ...v1, ...v2],
  };
}

// ── WB-007 旋转: an element rotated about its center, selected to show the rotate handle. ──
function rotationScene(): WhiteboardScene {
  return {
    id: "board_rotation",
    description:
      "WB-007 旋转：元素绕中心旋转，选中态显示旋转手柄 + 随之旋转的选择框",
    elements: [
      {
        id: "rot-sticky",
        type: "sticky",
        x: 220,
        y: 140,
        width: 200,
        height: 140,
        text: "旋转的便签",
        rotation: 0.4,
        schemaVersion: SCENE_SCHEMA_VERSION,
      },
      {
        id: "ref-rect",
        type: "rectangle",
        x: 480,
        y: 160,
        width: 160,
        height: 100,
        text: "未旋转对照",
        schemaVersion: SCENE_SCHEMA_VERSION,
      },
    ],
    selectedIds: ["rot-sticky"],
  };
}

// ── dagre 链路布局: a small DAG (boxes + arrows) laid out along its edges. ──
function dagreLayoutScene(): WhiteboardScene {
  const box = (
    id: string,
    x: number,
    y: number,
    text: string,
  ): SceneElement => ({
    id,
    type: "rectangle",
    x,
    y,
    width: 150,
    height: 64,
    text,
    schemaVersion: SCENE_SCHEMA_VERSION,
  });
  const edge = (id: string, from: string, to: string): SceneElement => ({
    id,
    type: "arrow",
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    start: { id: from },
    end: { id: to },
    points: [
      [0, 0],
      [1, 1],
    ],
    schemaVersion: SCENE_SCHEMA_VERSION,
  });
  // Deliberately messy starting positions; the projector re-flows them into a clean chain/tree.
  const raw: SceneElement[] = [
    box("n1", 40, 300, "需求"),
    box("n2", 360, 60, "设计"),
    box("n3", 380, 360, "实现"),
    box("n4", 700, 220, "评审"),
    edge("e1", "n1", "n2"),
    edge("e2", "n1", "n3"),
    edge("e3", "n2", "n4"),
    edge("e4", "n3", "n4"),
  ];
  const ids = new Set(raw.map((e) => e.id));
  return {
    id: "board_dagre_layout",
    description: "dagre 链路布局：按箭头依赖把选区重排成清晰的左→右分层图",
    elements: layoutDagre(raw, ids),
  };
}

export const WHITEBOARD_SCENES: WhiteboardScene[] = [
  progressOverlayScene(),
  crystallizedTeamScene(),
  fileArtifactScene(),
  iterateVersionsScene(),
  rotationScene(),
  dagreLayoutScene(),
];

/** Guards against an authored file scene that silently loses its `file` kind (WB-003). */
export function fileArtifactKindOf(scene: WhiteboardScene): string | null {
  const card = scene.elements.find((e) => e.type === "artifactCard" && e.ref);
  return card ? (card.artifactKind ?? null) : null;
}

/** Re-exported so the round-trip test can assert scenes are valid exportable vectors. */
export { artifactFromRun };
