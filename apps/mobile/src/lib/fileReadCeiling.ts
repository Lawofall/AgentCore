/**
 * Same-path ``file_read`` ceiling — short pointer, not fault red.
 * Mirror of desktop ``toolResult/fileReadCeiling`` (各端全新建；零共享业务逻辑).
 */
const CEILING_CUES = ["已多次读取", "勿再读", "再读次数"] as const;

export function isFileReadCeilingGuidance(
  toolName: string,
  result: string | null | undefined,
): boolean {
  if (toolName !== "file_read") return false;
  const text = result ?? "";
  if (!text) return false;
  return CEILING_CUES.some((cue) => text.includes(cue));
}
