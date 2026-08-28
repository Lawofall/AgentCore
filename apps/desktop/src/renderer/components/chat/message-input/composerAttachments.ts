import { getConversations } from "@/hooks/useConversations";
import { getFolders } from "@/hooks/useFolders";
import type { EntryKind, IndexedEntry } from "@/lib/fileIndex";
import type { FileSource } from "@/lib/fileSource";
import { hasInlineMarkers, plainText } from "@/lib/inlineBody";
import {
  buildLocalMentionPicks,
  collectRootUseEvents,
} from "@/services/mentionRoots";
import { resolveConversationLocalTarget } from "@/services/sidecarRouting";
import { createLocalRootSource } from "@/services/sources/localRootSource";
import { createCloudWorkspaceSource } from "@/services/sources/workspaceSource";
import {
  type WorkspaceBinding,
  getWorkspaceBinding,
} from "@/services/workspaceBinding";

/** 已选附件（含正文，仅发送时携带；气泡只展示元信息）。 */
export interface PendingAttachment {
  id: string;
  /** kind:sourceId:relPath，用于去重。 */
  key: string;
  name: string;
  /** 展示路径：优先工作区相对 ``attachments/…``，绝不含 OS 绝对路径。 */
  path: string;
  text: string;
  truncated: boolean;
  kind: EntryKind;
  /** 仅 kind=conversation：被引用对话的 id。 */
  conversationId?: string;
  /** 已在对话工作区时的相对路径（区内原路径，或 ``attachments/…``）。 */
  workspacePath?: string;
  /** 主进程暂存 id（草稿 / 待云端上传）；发送前 finalize / consume。 */
  stagingId?: string;
  /** 文件已在某授权根内：发送时若家仍是该根则引用、否则复制。 */
  citedRootId?: string;
  citedRelPath?: string;
  /** 二进制驻留：无 UTF-8 正文内联。 */
  binary?: boolean;
  /**
   * 浏览器草稿：尚无 conversationId 时暂存 File，建会话后由
   * ``ensureAttachmentResident`` PUT 到云工作区 ``attachments/``。
   * 不可进 localStorage；仅内存。
   */
  fileBlob?: File;
  /**
   * 附加即上传的进行态：chip 在用户操作后立刻出现，驻留/上传在后台跑
   * （见 `attachmentUploads`）。仅内存，不进 localStorage。
   */
  uploadState?: "uploading" | "error";
  /** `uploadState === "error"` 时的中文原因，挂在 chip 上。 */
  uploadError?: string;
}

/**
 * Pending `@Agent` chip（旁路 attachments，不上 MessageAttachment.kind）。
 * 发送时进 POST ``agent_mentions: [{ agent_id, role }]``。
 */
export interface PendingAgentMention {
  id: string;
  agentId: string;
  role: string;
}

export type MentionSectionId = "team" | "conversation" | "folder" | "file";

export const TEXT_PREVIEW_CAP = 256 * 1024;

export const CONV_MENTION_MSG_LIMIT = 40;
export const CONV_MENTION_CHAR_CAP = 60 * 1024;
/** `@Agent` 点名上限（与发送体 max 对齐）。 */
export const MAX_AGENT_MENTIONS = 10;
/** 空 `@` 时各索引分区默认条数。 */
export const EMPTY_MENTION_INDEX_LIMIT = 6;

/** True when the composer can send: non-blank text, or at least one pill. */
export function composerHasSendableDraft(
  value: string,
  attachments: ReadonlyArray<unknown>,
  agentMentions: ReadonlyArray<unknown> = [],
): boolean {
  return (
    Boolean(plainText(value).trim()) ||
    attachments.length > 0 ||
    agentMentions.length > 0 ||
    hasInlineMarkers(value)
  );
}

export function formatConversationContext(
  messages: { role: string; content: string }[],
): { text: string; truncated: boolean } {
  const usable = messages.filter((m) => m.content.trim());
  const recent = usable.slice(-CONV_MENTION_MSG_LIMIT);
  let truncated = recent.length < usable.length;
  const body = recent
    .map(
      (m) => `${m.role === "assistant" ? "助手" : "用户"}: ${m.content.trim()}`,
    )
    .join("\n\n");
  let text = body;
  if (text.length > CONV_MENTION_CHAR_CAP) {
    text = text.slice(text.length - CONV_MENTION_CHAR_CAP);
    truncated = true;
  }
  return { text: text.trim(), truncated };
}

export function detectMention(
  text: string,
  caret: number,
): { start: number; query: string } | null {
  let at = -1;
  for (let i = caret - 1; i >= 0; i--) {
    const ch = text[i];
    if (ch === "@") {
      at = i;
      break;
    }
    if (ch === " " || ch === "\n" || ch === "\t" || ch === "\uFFFC")
      return null;
  }
  if (at === -1) return null;
  const before = at === 0 ? "" : text[at - 1];
  if (before && !/\s/.test(before) && before !== "\uFFFC") return null;
  return { start: at, query: text.slice(at + 1, caret) };
}

/**
 * `@` 类型前缀：以「团队/对话/文件/文件夹」或英文 agent/file/dir/folder/conv 开头时
 * 只保留对应分区；前缀后的剩余串作过滤词。
 */
export function parseMentionFilter(rawQuery: string): {
  section: MentionSectionId | null;
  filter: string;
} {
  const q = rawQuery.trimStart();
  // 较长中文前缀优先；英文忽略大小写。
  const rules: { re: RegExp; section: MentionSectionId }[] = [
    { re: /^(文件夹|folder|dir)\s*/i, section: "folder" },
    { re: /^(文件|file)\s*/i, section: "file" },
    { re: /^(对话|conv(?:ersation)?)\s*/i, section: "conversation" },
    { re: /^(团队|agent)\s*/i, section: "team" },
  ];
  for (const { re, section } of rules) {
    const m = q.match(re);
    if (m) return { section, filter: q.slice(m[0].length) };
  }
  return { section: null, filter: q };
}

/** 近期对话 → IndexedEntry；排除当前会话，可选标题子串过滤。 */
export function pickRecentConversations(
  list: ReadonlyArray<{ id: string; title: string }>,
  excludeId: string | null,
  filter: string,
  limit = EMPTY_MENTION_INDEX_LIMIT,
): IndexedEntry[] {
  const q = filter.trim().toLowerCase();
  let rows = list.filter((c) => c.id !== excludeId);
  if (q) {
    rows = rows.filter((c) => (c.title || "").toLowerCase().includes(q));
  }
  return rows.slice(0, limit).map((c) => ({
    sourceId: "conversation",
    sourceLabel: "对话",
    relPath: c.id,
    name: c.title || "未命名对话",
    display: c.title || "未命名对话",
    kind: "conversation" as const,
  }));
}

export async function buildMentionSources(
  conversationId: string | null,
): Promise<FileSource[]> {
  let binding: WorkspaceBinding | null = null;
  if (conversationId) {
    try {
      binding = await getWorkspaceBinding(conversationId);
      if (binding.mode === "cloud") {
        return [createCloudWorkspaceSource(`conv:${conversationId}`, "工作区")];
      }
    } catch {
      binding = null;
    }
  }

  const listed = (await window.fsApi?.listRoots()) ?? [];
  const roots = listed.map((r) => ({
    id: r.id,
    name: r.name,
    absPath: r.absPath,
  }));

  let subpath = "";
  if (conversationId && binding?.mode === "local" && binding.rootId) {
    try {
      const target = await resolveConversationLocalTarget(conversationId);
      if (target?.rootId === binding.rootId) subpath = target.subpath ?? "";
    } catch {
      // store/cache miss — index the bound root itself
    }
  }

  const picks = buildLocalMentionPicks({
    binding,
    roots,
    subpath,
    uses: collectRootUseEvents(getConversations(), getFolders()),
  });
  return picks.map((p) => createLocalRootSource(p.id, p.label, p.subpath));
}
