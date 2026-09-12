import { ensureBoardConversation } from "@/services/boards";
import { streamConversation } from "@/services/streamConversation";
import type { ElementType, SceneElement } from "@/whiteboard";

/**
 * Run one AI turn on a board's dedicated conversation (AI协作白板.md §六 M2 入口).
 *
 * The board counterpart of the chat composer's send: it (1) lazily mints + binds the
 * board's AI conversation (idempotent — the server returns the existing one after the
 * first call) so the turn is recognized as a 白板会话 and the CEO gets `board_ops`, then
 * (2) streams the turn on that conversation. Every SSE event is dispatched through the
 * shared pump, so a `board_op_required` lands on this board's registered applier (the
 * open canvas) and draws — that is the whole point of the turn.
 *
 * Unlike the chat composer pipeline (`useComposerSend`) this targets the board's
 * OWN conversation (not the active chat),
 * and deliberately doesn't seed the conversation store with an optimistic user bubble:
 * the canvas has no chat surface, the server persists the transcript authoritatively, and
 * the visible effect is the canvas mutating. Errors propagate so the canvas can show its
 * own (toast) feedback — a user abort surfaces as an `AbortError` for the caller to ignore.
 */
export interface BoardTurnOptions {
  signal?: AbortSignal;
  /** Called with the board's conversation id the instant it resolves — BEFORE the stream
   * starts — so the host can bind any live UI to this id while the turn runs. */
  onConversation?: (conversationId: string) => void;
}

export async function sendBoardTurn(
  boardId: string,
  content: string,
  options: BoardTurnOptions = {},
): Promise<void> {
  const { conversation_id } = await ensureBoardConversation(boardId);
  options.onConversation?.(conversation_id);
  await streamConversation({
    conversationId: conversation_id,
    content,
    delivery: "steer",
    signal: options.signal,
  });
}

/**
 * Render a selection into a compact, model-readable list for the「整理选区」prompt.
 *
 * One line per selected element: its real `id` (so the AI can target it with `board_ops`
 * move / set_text / group), its shape, position, and any text. Elements not in the scene
 * (a stale selection id) are skipped. Pure — unit-tested without the engine.
 */
export function describeSelection(
  elements: readonly SceneElement[],
  selectedIds: readonly string[],
): string {
  const byId = new Map(elements.map((el) => [el.id, el]));
  const lines: string[] = [];
  for (const id of selectedIds) {
    const el = byId.get(id);
    if (!el) continue;
    const where = ` @(${Math.round(el.x)},${Math.round(el.y)})`;
    const text = el.text?.trim() ? `：“${el.text.trim()}”` : "";
    lines.push(`- [${id}] ${el.type}${where}${text}`);
  }
  return lines.join("\n");
}

// Element types whose meaning lives in PIXELS, not in (type/text/pos) — the AI can't read
// them from a text description, so the「整理选区」prompt routes them through `board_read`
// (§九 混合 payload) instead of a useless "- [id] freedraw @(x,y)" line. `freedraw` (手绘)
// and `image` (粘贴/拖入的截图) both qualify; structured types stay describable as text.
const VISUAL_TYPES: ReadonlySet<ElementType> = new Set<ElementType>([
  "freedraw",
  "image",
]);

/**
 * Split a selection into the ids the AI reads as TEXT vs the ids it must SEE (§九 混合 payload).
 *
 * `structuredIds` go to {@link describeSelection} (the AI targets them by real id with
 * `board_ops`); `visualIds` are hand-drawn / screenshot content the prompt tells the CEO to
 * `board_read` (on-demand rasterization). Stale selection ids (not in the scene) are dropped.
 * Pure — unit-tested without the engine.
 */
export function partitionSelection(
  elements: readonly SceneElement[],
  selectedIds: readonly string[],
): { structuredIds: string[]; visualIds: string[] } {
  const byId = new Map(elements.map((el) => [el.id, el]));
  const structuredIds: string[] = [];
  const visualIds: string[] = [];
  for (const id of selectedIds) {
    const el = byId.get(id);
    if (!el) continue;
    (VISUAL_TYPES.has(el.type) ? visualIds : structuredIds).push(id);
  }
  return { structuredIds, visualIds };
}

/**
 * Compose the「整理选区」turn prompt, mixing structured + visual payloads (§九).
 *
 * Structured elements are rendered as text ({@link describeSelection}) so the CEO targets
 * them by real id with `board_ops`. When the selection also holds hand-drawn / screenshot
 * (`freedraw`) content, the prompt instructs the CEO to `board_read` those ids first — text
 * can't convey them — replacing the former describeSelection-only downgrade that silently
 * fed the AI a meaningless "- [id] freedraw @(x,y)" line it could never act on.
 */
export function organizeSelectionPrompt(
  elements: readonly SceneElement[],
  selectedIds: readonly string[],
): string {
  const { structuredIds, visualIds } = partitionSelection(
    elements,
    selectedIds,
  );
  const out: string[] = [];

  if (visualIds.length > 0) {
    out.push(
      "我在白板上圈选了一块内容，请你照着它帮我整理 / 落成更清晰的白板结构。",
      `选区里有手绘 / 截图元素（id：${visualIds.join("、")}），文字描述不了——请先调用 board_read 工具读懂这些 id 的内容与意图，再动手。`,
    );
  } else {
    out.push(
      "我在白板上选中了以下元素，请你用 board_ops 帮我把它们整理得更清晰、更有条理",
      "（例如：对齐、重新排布、按主题分组、补充必要的连线或简短说明）。",
    );
  }
  out.push(
    "对已存在的元素请用它们的真实 id 操作（board_ops）；需要新增节点时给 ref 再连线。",
  );

  const structuredDesc = describeSelection(elements, structuredIds);
  if (structuredDesc) {
    out.push("", "选区中的结构化元素：", structuredDesc);
  }
  return out.join("\n");
}

/**
 * Whether「照这实现」has a usable brief: non-empty text on a structured element, and/or a
 * visual path (`freedraw` / `image`). Structured-only empty rectangles/shapes (no sticky/text
 * citation, no visuals) would otherwise ship a hollow brief — the canvas should tip + block
 * instead of sending (sample `32b78c65`). Does NOT force vision: sticky/text alone is enough.
 */
export function selectionHasImplementBrief(
  elements: readonly SceneElement[],
  selectedIds: readonly string[],
): boolean {
  const { structuredIds, visualIds } = partitionSelection(
    elements,
    selectedIds,
  );
  if (visualIds.length > 0) return true;
  const byId = new Map(elements.map((el) => [el.id, el]));
  for (const id of structuredIds) {
    const el = byId.get(id);
    if (el?.text?.trim()) return true;
  }
  return false;
}

/** Toast copy when {@link selectionHasImplementBrief} is false — tip, don't silent-send. */
export const EMPTY_IMPLEMENT_BRIEF_HINT =
  "选区没有文字需求：请先写便签 / 选有文字的元素后再照这实现（手绘/截图可不写字）";

/**
 * Compose the「让团队照这实现」turn prompt.
 *
 * Hands the selection / `frame` to the CEO as the requirement brief and asks it to
 * assemble the team and implement. Orchestration stays the CEO's call (`delegate` /
 * debate / 单干). Mixes structured + visual payloads like {@link organizeSelectionPrompt}:
 * structured elements go as text the CEO targets by real id; hand-drawn / screenshot ids
 * are flagged for `board_read`. Finished work lands in the workspace; the board is only
 * for drawing structure via `board_ops`.
 *
 * Callers must gate with {@link selectionHasImplementBrief} first — this composer still
 * accepts empty structured selections for unit isolation, but the canvas must not send them.
 */
export function implementSelectionPrompt(
  elements: readonly SceneElement[],
  selectedIds: readonly string[],
): string {
  const { structuredIds, visualIds } = partitionSelection(
    elements,
    selectedIds,
  );
  const out: string[] = [
    "我在白板上圈选了一块内容，作为这次的需求 brief——请你带团队照着它把东西做出来。",
    "你作为 CEO 自行判断怎么拆解：需要时委派合适的队员并行推进、必要时让他们辩论，最终交付可用的产物。",
  ];
  if (visualIds.length > 0) {
    out.push(
      `选区里有手绘 / 截图元素（id：${visualIds.join("、")}），文字描述不了——请先调用 board_read 工具读懂这些 id 的内容与意图，再动手。`,
    );
  }
  out.push(
    "成品交到工作区。若要在白板上落结构，用 board_ops；对已存在的元素请用它们的真实 id 操作。",
  );

  const structuredDesc = describeSelection(elements, structuredIds);
  if (structuredDesc) {
    out.push("", "选区中的结构化元素（即需求要点）：", structuredDesc);
  }
  return out.join("\n");
}
