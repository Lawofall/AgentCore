import { SCENE_SCHEMA_VERSION, type SceneElement } from "@/whiteboard";
import { layoutDagre } from "@/whiteboard/layoutDagre";

/**
 * Offline preview scene vectors for the self-built whiteboard canvas (AI协作白板.md).
 *
 * The chat preview (`#/preview`) replays backend SSE golden vectors; the whiteboard is a
 * separate canvas surface whose "vector" is a SCENE (a list of {@link SceneElement}) rendered by
 * the real {@link WhiteboardCanvas}. Scenes here are authored directly or DERIVED from the
 * dagre auto-layout, so previewing exercises the same pure logic production uses.
 *
 * These back the offline preview route (`#/preview/whiteboard`) and its screenshot smoke gate
 * (`scripts/shoot-whiteboard.mjs`). Scenes serialize through the board scene format
 * (round-tripped in the unit test), so they are exportable board vectors.
 */

export interface WhiteboardScene {
  id: string;
  description: string;
  elements: SceneElement[];
  /** Element ids to preselect (rotation handle / selected-chrome states). */
  selectedIds?: string[];
}

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
  rotationScene(),
  dagreLayoutScene(),
];
