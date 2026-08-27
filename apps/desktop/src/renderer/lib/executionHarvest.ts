/**
 * 历史库合成「用户」行（``usage.origin=execution_harvest`` / 「【系统收口】」前缀）。
 * 新路径不再写这类行；读侧隐藏，避免露出当时的模型提示词。
 */

export const EXECUTION_HARVEST_ORIGIN = "execution_harvest";

/** 历史通道死 / 无 LLM 回退行。 */
export const EXECUTION_HARVEST_FALLBACK_ORIGIN = "execution_harvest_fallback";

/** 历史合成用户行正文前缀。 */
export const HARVEST_USER_CONTENT_PREFIX = "【系统收口】";

export function isExecutionHarvestMessage(msg: {
  role: string;
  content: string;
  origin?: string | null;
}): boolean {
  if (msg.role !== "user") return false;
  if (msg.origin === EXECUTION_HARVEST_ORIGIN) return true;
  return msg.content.startsWith(HARVEST_USER_CONTENT_PREFIX);
}

/** Historical outbox / RecordTurnRequest provenance — not a live write path. */
export function isHarvestWritebackOrigin(origin?: string | null): boolean {
  return (
    origin === EXECUTION_HARVEST_ORIGIN ||
    origin === EXECUTION_HARVEST_FALLBACK_ORIGIN
  );
}

/** Drain ack for leftover harvest write-back (origin or harvest_kind). */
export function isHarvestWritebackAck(payload: {
  origin?: string | null;
  harvestKind?: string | null;
}): boolean {
  if (isHarvestWritebackOrigin(payload.origin)) return true;
  return Boolean(payload.harvestKind);
}
