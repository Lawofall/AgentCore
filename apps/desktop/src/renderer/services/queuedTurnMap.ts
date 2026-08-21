import type {
  OutgoingAgentMention,
  OutgoingAttachment,
} from "@/services/streamConversation";

function isAttachmentKind(
  value: unknown,
): value is NonNullable<OutgoingAttachment["kind"]> {
  return value === "file" || value === "dir" || value === "conversation";
}

/** 快照 / 出队帧附件 → 发送载荷。原样保留 ``path`` / ``workspace_path``，禁止另造路径。 */
export function mapQueuedAttachments(
  raw: unknown,
): OutgoingAttachment[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: OutgoingAttachment[] = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const a = row as Record<string, unknown>;
    const name = typeof a.name === "string" ? a.name : "";
    const path = typeof a.path === "string" ? a.path : "";
    if (!name || !path) continue;
    const mapped: OutgoingAttachment = {
      name,
      path,
      text: typeof a.text === "string" ? a.text : "",
      truncated: a.truncated === true,
    };
    if (isAttachmentKind(a.kind)) mapped.kind = a.kind;
    if (typeof a.conversation_id === "string" && a.conversation_id) {
      mapped.conversation_id = a.conversation_id;
    }
    if (a.binary === true) mapped.binary = true;
    if (typeof a.workspace_path === "string" && a.workspace_path) {
      mapped.workspace_path = a.workspace_path;
    }
    out.push(mapped);
  }
  return out.length > 0 ? out : undefined;
}

export function mapQueuedMentions(
  raw: unknown,
): OutgoingAgentMention[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: OutgoingAgentMention[] = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const m = row as Record<string, unknown>;
    const agentId = typeof m.agent_id === "string" ? m.agent_id.trim() : "";
    const role = typeof m.role === "string" ? m.role.trim() : "";
    if (!agentId || !role) continue;
    out.push({ agent_id: agentId, role });
  }
  return out.length > 0 ? out : undefined;
}
