/**
 * ``turn_queue_started`` 出队插主时间线用户泡。
 * 放宽读 payload（旧帧可能无 content；现行契约已带）。
 * 与 beginTurn2 共用 queue_id 幂等，勿双泡。
 */

export type QueuedUserBubbleFields = {
  userText: string;
  attachments?: { name: string; truncated?: boolean }[];
  agentMentions?: { agentId: string; role: string }[];
};

export type ParsedQueueStartedUser = {
  queueId: string;
  bubble: QueuedUserBubbleFields | null;
};

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function parseAttachments(
  raw: unknown,
): { name: string; truncated?: boolean }[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: { name: string; truncated?: boolean }[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const name = asString((item as { name?: unknown }).name).trim();
    if (!name) continue;
    const truncated = (item as { truncated?: unknown }).truncated === true;
    out.push(truncated ? { name, truncated: true } : { name });
  }
  return out.length > 0 ? out : undefined;
}

function parseMentions(
  raw: unknown,
): { agentId: string; role: string }[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  const out: { agentId: string; role: string }[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const row = item as {
      agent_id?: unknown;
      agentId?: unknown;
      role?: unknown;
    };
    const agentId = asString(row.agent_id) || asString(row.agentId);
    const role = asString(row.role);
    if (!agentId && !role) continue;
    out.push({ agentId, role: role || agentId });
  }
  return out.length > 0 ? out : undefined;
}

function hasUserSurface(bubble: QueuedUserBubbleFields): boolean {
  return Boolean(
    bubble.userText.trim() ||
      bubble.attachments?.length ||
      bubble.agentMentions?.length,
  );
}

/**
 * 放宽读 ``turn_queue_started`` payload。
 * ``queue_id`` 缺失 → null；无正文/附件/点名 → ``bubble: null``（仍可清条）。
 */
export function parseTurnQueueStartedUser(
  payload: unknown,
): ParsedQueueStartedUser | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Record<string, unknown>;
  const queueId = asString(p.queue_id).trim();
  if (!queueId) return null;
  const bubble: QueuedUserBubbleFields = {
    userText: asString(p.content),
    attachments: parseAttachments(p.attachments),
    agentMentions: parseMentions(p.agent_mentions ?? p.agentMentions),
  };
  return {
    queueId,
    bubble: hasUserSurface(bubble) ? bubble : null,
  };
}
