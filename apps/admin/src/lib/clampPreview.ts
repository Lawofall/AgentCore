/** Cap dumped tool JSON so a grep / file_read cannot inflate the replay column. */
export const PREVIEW_CHAR_CAP = 4000;

export function clampPreview(
  text: string,
  cap = PREVIEW_CHAR_CAP,
): { text: string; truncated: boolean } {
  if (text.length <= cap) return { text, truncated: false };
  return { text: `${text.slice(0, cap)}\n…`, truncated: true };
}

/** Size + wrap for a replay `<pre>` that must stay inside the reading column. */
export const CLAMPED_PRE_CLASS =
  "max-h-48 max-w-full overflow-auto whitespace-pre-wrap break-words [overflow-wrap:anywhere]";
