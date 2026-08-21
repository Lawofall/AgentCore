/**
 * Same-path ``file_read`` ceiling (Wave3 B / R1): engine returns a short
 * success pointer (legacy tapes may still be ``status:"error"``). UI reads
 * as guidance — not the same red fault chrome as IO/missing-file failures.
 *
 * No wire enum. Detect via tool name + stable backend copy cues
 * (see ``file_ops._file_read_same_window_hit``).
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
