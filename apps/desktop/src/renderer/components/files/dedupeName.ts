/**
 * 为「复制-粘贴」算一个在目标目录中不冲突的名字：命中即追加「 副本」，再冲突则「 副本 2」…
 * 保留扩展名（`a.txt` → `a 副本.txt`）。前导点文件（`.env`）按无扩展名整体处理。
 */
export function dedupeName(name: string, existing: Set<string>): string {
  if (!existing.has(name)) return name;
  const dot = name.lastIndexOf(".");
  const hasExt = dot > 0; // 前导点（dot===0）不算扩展名
  const stem = hasExt ? name.slice(0, dot) : name;
  const ext = hasExt ? name.slice(dot) : "";
  let candidate = `${stem} 副本${ext}`;
  let n = 2;
  while (existing.has(candidate)) {
    candidate = `${stem} 副本 ${n}${ext}`;
    n++;
  }
  return candidate;
}

/** Finder-style default name for a newly created directory (not a copy). */
export const UNTITLED_FOLDER_NAME = "未命名文件夹";

/** Prompt-catalog grouping folder (工具箱「新建夹」). */
export const UNTITLED_PROMPT_FOLDER_NAME = "未命名夹";

/**
 * Generated placeholder (`未命名夹`, then `未命名夹 (2)`…), not a name the person chose.
 * `(1)` is not part of that sequence.
 */
export function isUntitledPromptFolderName(name: string): boolean {
  if (name === UNTITLED_PROMPT_FOLDER_NAME) return true;
  const prefix = `${UNTITLED_PROMPT_FOLDER_NAME} (`;
  if (!name.startsWith(prefix) || !name.endsWith(")")) return false;
  const n = name.slice(prefix.length, -1);
  return /^[1-9]\d*$/.test(n) && Number(n) >= 2;
}

/**
 * Same numbering as server `unique_sibling_name`: `base`, then `base (2)`…
 * Comparison is case-insensitive so `报告` and `报告` cannot both land.
 */
export function uniqueNumberedName(
  base: string,
  existing: Iterable<string>,
): string {
  const used = new Set(
    [...(existing instanceof Set ? existing : new Set(existing))].map((s) =>
      s.toLowerCase(),
    ),
  );
  if (!used.has(base.toLowerCase())) return base;
  let n = 2;
  while (used.has(`${base} (${n})`.toLowerCase())) n++;
  return `${base} (${n})`;
}

export function uniqueUntitledFolder(existing: Iterable<string>): string {
  return uniqueNumberedName(UNTITLED_FOLDER_NAME, existing);
}
