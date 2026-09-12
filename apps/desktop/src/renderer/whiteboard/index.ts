/** Self-built whiteboard engine (AI协作白板.md §六 自研引擎架构) — public surface. */

export {
  WhiteboardCanvas,
  type WhiteboardCanvasProps,
} from "./WhiteboardCanvas";
export { parseScene, serializeScene } from "./scene";
export { SCENE_SCHEMA_VERSION } from "./types";
export type {
  BoardScenePayload,
  ElementType,
  SceneElement,
  Tool,
  Viewport,
  WhiteboardApi,
} from "./types";
