// 回合产物盘点 —— delivery 验收路径与工具变更的纯数据源。
//
// 聊天流产物清单卡已撤：打开入口是终稿路径可点 + 工作区树。「查看改动」走右坞。
//
// 主清单解析仍只认 ``delivery_status.artifacts``（accepted+rejected 验收态）。
// 无该字段 / 空数组 → 空清单（不 silent 降级扫工具列表）。后端按 execution 把各波
// 声明且落盘路径并进最新一条事件；未声明备份不在清单里。导出件（.docx / .pdf）只存在于
// 工具自报的产物行里——工具入参只有源 md，故任何按参数合成的清单都会漏掉它。
//
// ``promotedFrom`` 只兼容历史 ``delivery_status.promoted``（``promote_product`` 已撤销；
// 新回合不再搬家）。不按「成品 / 过程材料」分组——位置看路径；文件树把
// ``AgentCore/`` 标成 ``.agentcore`` 并钉顶。
//
// 中间稿折叠：行里自报的 ``derived_from`` 是唯一依据（见 {@link splitExportedSources}），
// 不按扩展名 / 工具名猜派生关系；折叠只降级、不删除。
//
// 工具列表（process / execution）仍供「查看改动」（TurnFileChangesReview /
// ConversationChangesPanel）：写/改/删/移经 builtin file_ops，抽成功变更 + 参数预览。
// 不把工具名当交付成功。
//
// A1「查看改动」：从工具参数附带只读预览（str_replace → old/new；file_write → 写入正文；
// delete/move → 元信息）。无 before 快照、不改写盘契约。
//
// 纯函数、只读已有运行时状态，不碰协议 fold（故不触发 conformance、零持久化）。
// 真相仍以工作区文件树为准。

import type { Execution } from "@/stores/execution";
import type {
  DeliveryPromotion,
  DeliveryStatusPayload,
  ProcessStep,
} from "@/types/events";
import { toWorkspaceRelPath } from "@shared/workspace-path";

/** 文件变更类型 —— 决定图标 / 文案 / 是否可预览（删除态无文件可看）。 */
export type FileOp = "write" | "edit" | "delete" | "move";

/** 路径级验收态（delivery_status.artifacts）。 */
export type ArtifactAcceptance = "accepted" | "rejected";

/** 工具参数派生的只读改动预览（A1）；缺参时为 undefined。 */
export type FileChangePreview =
  | { kind: "edit"; oldText: string; newText: string }
  | { kind: "write"; content: string; mode: "overwrite" | "append" }
  | { kind: "delete" }
  | { kind: "move"; fromPath: string };

export interface FileArtifact {
  /** 变更后的路径（move 取目的地，其余取 path）；同时作为回合内去重键。 */
  path: string;
  /** 展示用文件名（path 的 basename）。 */
  name: string;
  /** 工具源才有；验收源可缺（主清单不再用写入/编辑标签）。 */
  op?: FileOp;
  /** 仅 move：源路径，用于「源 → 目的」展示。 */
  fromPath?: string;
  /** A1：只读「查看改动」用的参数侧预览。 */
  change?: FileChangePreview;
  /** 路径验收态（有则主清单按态分行；通过行不打徽章，未通过标「未通过」，不显示写入/编辑）。 */
  acceptance?: ArtifactAcceptance;
  acceptanceReason?: string;
  acceptanceDetail?: string;
  /**
   * 已归位成品的过程稿旧路径（`path` 已是归位后的新路径，旧路径盘上不再存在）。
   * 不据此分组；位置以 `path` 为准。
   */
  promotedFrom?: string;
  /** 产出工具自报的产物类型（`md` / `docx` / `pdf` / `code` / …）；未自报时缺省。 */
  kind?: string;
  /** 自报的派生源：本产物是那份文件的导出件（`md_to_docx`：docx ← 源 md）。 */
  derivedFrom?: string;
  /**
   * 落地 desk（`folder:…` / `conv:…`）。来自 delivery `workspace_id`；
   * 缺省时打开完整预览回退会话工作区 wsId。
   */
  workspaceId?: string;
}

/** 视觉 critic 落盘的预览截图：`kind=image` 且自报 `derivedFrom`（源 HTML 等）。 */
export function isPreviewScreenshot(artifact: FileArtifact): boolean {
  return artifact.kind === "image" && !!artifact.derivedFrom?.trim();
}

/**
 * 写文件的 builtin 工具名 → 变更类型。只读工具（file_read / list 等）与未知/外部
 * 工具不在表内 —— 它们不产出文件，不进卡。
 */
const OP_BY_TOOL: Record<string, FileOp> = {
  file_write: "write",
  file_append: "write",
  str_replace: "edit",
  file_delete: "delete",
  file_move: "move",
  file_copy: "write",
};

function basename(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

function asStr(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function changeFromTool(
  toolName: string,
  args: Record<string, unknown>,
  op: FileOp,
  fromPath?: string,
): FileChangePreview | undefined {
  if (op === "edit") {
    const oldText = asStr(args.old_string);
    const newText = asStr(args.new_string);
    // 允许空串（删光 / 写成空），但两边都缺则无预览。
    if (!("old_string" in args) || !("new_string" in args)) return undefined;
    return { kind: "edit", oldText, newText };
  }
  if (op === "write") {
    if (toolName === "file_copy") return undefined;
    const content = asStr(args.content);
    if (!("content" in args)) return undefined;
    return {
      kind: "write",
      content,
      mode: toolName === "file_append" ? "append" : "overwrite",
    };
  }
  if (op === "delete") return { kind: "delete" };
  if (op === "move") return { kind: "move", fromPath: fromPath ?? "" };
  return undefined;
}

/** 把一次工具调用映射成文件产物；非文件工具 / 未成功 / 缺路径 → null（不进卡）。 */
function artifactFromTool(
  toolName: string,
  args: Record<string, unknown>,
  succeeded: boolean,
): FileArtifact | null {
  if (!succeeded) return null;
  const op = OP_BY_TOOL[toolName];
  if (!op) return null;
  if (op === "move") {
    const to = toWorkspaceRelPath(asStr(args.destination));
    if (!to) return null;
    const fromRaw = asStr(args.source);
    const from = fromRaw ? toWorkspaceRelPath(fromRaw) : "";
    return {
      path: to,
      name: basename(to),
      op,
      fromPath: from || undefined,
      change: changeFromTool(toolName, args, op, from || undefined),
    };
  }
  const path = toWorkspaceRelPath(asStr(args.path));
  if (!path) return null;
  return {
    path,
    name: basename(path),
    op,
    change: changeFromTool(toolName, args, op),
  };
}

/**
 * 按最终路径折叠：同一文件回合内多次改只留最后一次动作（= 回合终态），保留首见顺序。
 * 幂等，故可对已去重的列表再 merge 而不出错。
 */
function dedupe(ordered: FileArtifact[]): FileArtifact[] {
  const byPath = new Map<string, FileArtifact>();
  const order: string[] = [];
  for (const a of ordered) {
    if (!byPath.has(a.path)) order.push(a.path);
    byPath.set(a.path, a);
  }
  return order.map((p) => byPath.get(p) as FileArtifact);
}

/** 是否有可展开的改动预览（至少一条带 change）。 */
export function hasChangePreviews(artifacts: FileArtifact[]): boolean {
  return artifacts.some((a) => a.change != null);
}

/**
 * 历史 ``promoted`` 对照表（键 = 路径）。``promote_product`` 已撤销，新回合不再搬家；
 * 本表只为旧会话回放仍带 ``{from,to}`` 的卡。
 */
function promotionsByPath(
  deliveryStatus: DeliveryStatusPayload,
): Map<string, DeliveryPromotion> {
  const out = new Map<string, DeliveryPromotion>();
  if (!Array.isArray(deliveryStatus.promoted)) return out;
  for (const row of deliveryStatus.promoted) {
    const from = toWorkspaceRelPath(asStr(row.from));
    const to = toWorkspaceRelPath(asStr(row.to));
    if (!from || !to) continue;
    const entry: DeliveryPromotion = { from, to };
    out.set(from, entry);
    out.set(to, entry);
  }
  return out;
}

/**
 * 主清单：有 ``deliveryStatus.artifacts`` 字段时用之（含空数组）；
 * 缺字段 → null（调用方应视为空，勿再扫工具列表）。
 *
 * 归位过的行落在新路径上（后端已改写；本地按对照表兜一道），并记下旧路径供分组。
 */
export function fileArtifactsFromDeliveryStatus(
  deliveryStatus: DeliveryStatusPayload | null | undefined,
): FileArtifact[] | null {
  if (!deliveryStatus || !Array.isArray(deliveryStatus.artifacts)) return null;
  const promotions = promotionsByPath(deliveryStatus);
  const out: FileArtifact[] = [];
  for (const row of deliveryStatus.artifacts) {
    const listedPath = toWorkspaceRelPath(asStr(row.path));
    if (!listedPath) continue;
    const status = row.status;
    if (status !== "accepted" && status !== "rejected") continue;
    // 只有 accepted 可归位（质量态是位置态的前提）。
    const promotion =
      status === "accepted" ? promotions.get(listedPath) : undefined;
    const path = promotion?.to ?? listedPath;
    const workspaceId =
      typeof row.workspace_id === "string" && row.workspace_id.trim()
        ? row.workspace_id.trim()
        : undefined;
    // 派生源同样跟着归位改路径，否则源被归位后中间稿折叠会认不出自己的源。
    const listedSource = toWorkspaceRelPath(asStr(row.derived_from));
    const derivedFrom = promotions.get(listedSource)?.to ?? listedSource;
    out.push({
      path,
      name: basename(path),
      acceptance: status,
      acceptanceReason: row.reason,
      acceptanceDetail: row.detail,
      ...(workspaceId ? { workspaceId } : {}),
      ...(row.kind ? { kind: row.kind } : {}),
      ...(derivedFrom ? { derivedFrom } : {}),
      ...(promotion ? { promotedFrom: promotion.from } : {}),
    });
  }
  return dedupe(out);
}

/**
 * 拆出「主推件 / 被折叠的中间稿」——口径与后端 `fold_exported_sources` 一致。
 *
 * 一件**已验收**产物的 `derivedFrom` 指向另一件**已验收**产物时，后者是它的源：用户要的是
 * 导出件（`md_to_docx`：docx ← 源 md），并列两份会让人把 .md 当成「那份 Word」。只认工具
 * 自报的派生关系——不看扩展名、不看工具名，没自报就一份都不降级。
 *
 * 折叠 ≠ 删除：中间稿仍在返回值里，调用方须留可展开的入口。导出件本身永不被藏——源未验收
 * 时无从折叠；自报成环导致主清单会被清空时整体不折叠。``kind=image`` 的预览截图虽带
 * ``derivedFrom``（被截 HTML），但不把源页面降为中间稿。
 */
export function splitExportedSources(artifacts: FileArtifact[]): {
  primary: FileArtifact[];
  intermediate: FileArtifact[];
} {
  const acceptedPaths = new Set(
    artifacts.filter((a) => a.acceptance === "accepted").map((a) => a.path),
  );
  const sources = new Set<string>();
  for (const a of artifacts) {
    if (a.acceptance !== "accepted") continue;
    if (a.kind === "image") continue;
    const src = a.derivedFrom;
    if (!src || src === a.path) continue;
    if (acceptedPaths.has(src)) sources.add(src);
  }
  if (sources.size === 0) return { primary: artifacts, intermediate: [] };
  const primary = artifacts.filter((a) => !sources.has(a.path));
  if (primary.length === 0) return { primary: artifacts, intermediate: [] };
  return {
    primary,
    intermediate: artifacts.filter((a) => sources.has(a.path)),
  };
}

/** 单聊：从内联过程时间线（message.process）抽成功的文件变更（「查看改动」旁路）。 */
export function fileArtifactsFromProcess(
  process: ProcessStep[] | undefined,
): FileArtifact[] {
  if (!process?.length) return [];
  const out: FileArtifact[] = [];
  for (const step of process) {
    if (step.kind !== "tool") continue;
    const a = artifactFromTool(
      step.tool_name,
      step.arguments,
      step.status === "success",
    );
    if (a) out.push(a);
  }
  return dedupe(out);
}

/** 多 Agent：从团队执行快照各 agent（含 CEO captain run）的 toolCalls 跨 worker 汇总。 */
export function fileArtifactsFromExecution(
  execution: Execution | null,
): FileArtifact[] {
  if (!execution) return [];
  const out: FileArtifact[] = [];
  for (const agent of execution.agents) {
    for (const tc of agent.toolCalls) {
      const a = artifactFromTool(
        tc.toolName,
        tc.arguments,
        tc.status === "success",
      );
      if (a) out.push(a);
    }
  }
  return dedupe(out);
}

/** 合并多个来源（如多 Agent 回合的 CEO process + 团队 execution）后统一去重。 */
export function mergeArtifacts(...lists: FileArtifact[][]): FileArtifact[] {
  return dedupe(lists.flat());
}

/** 主清单解析：只认验收 artifacts；缺字段 / 空 → []（不降级工具列表）。 */
export function resolveFileArtifactsForCard(
  deliveryStatus: DeliveryStatusPayload | null | undefined,
): FileArtifact[] {
  return fileArtifactsFromDeliveryStatus(deliveryStatus) ?? [];
}
