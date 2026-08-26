/**
 * 终稿里的工作区相对文件路径（CEO【产物路径】口径）。
 * 聊天流产物清单卡已撤：打开入口 = 正文里这些路径可点。
 *
 * 保守：必须含 `/` 且末段有扩展名；拒绝 URL / MIME。不扫无斜杠的裸文件名。
 */

const FILE_EXT = /\.[A-Za-z0-9]{1,10}$/;
/** 路径段：字母数字、中文、点、下划线、连字符。不含括号/冒号以免吞 URL 与「（path）」。 */
const SEG = String.raw`[^\s/\\:()（）【】「」『』<>，。；！？、,"']+`;
const PATH_INNER = new RegExp(`(?:${SEG}/)+${SEG}\\.[A-Za-z0-9]{1,10}`, "g");
const BOUNDARY_BEFORE = /[\s"'`（(【「『<]/;
const MIME_PREFIX = /^(?:text|image|audio|video|application)\//i;

export function normalizeWorkspaceRelPath(raw: string): string {
  return raw.trim().replace(/\\/g, "/").replace(/^\/+/, "");
}

export function isWorkspaceFilePath(raw: string): boolean {
  const p = normalizeWorkspaceRelPath(raw);
  if (!p || p.includes("://")) return false;
  if (/^(?:https?|ftp|mailto|file):/i.test(p)) return false;
  if (MIME_PREFIX.test(p)) return false;
  if (!p.includes("/")) return false;
  const segs = p.split("/");
  if (segs.some((s) => !s || s === "." || s === "..")) return false;
  const last = segs[segs.length - 1] ?? "";
  return FILE_EXT.test(last);
}

export type WorkspacePathHit = {
  start: number;
  end: number;
  path: string;
};

/** 在纯文本里找出可点路径（不含围栏代码）。 */
export function findWorkspaceFilePaths(value: string): WorkspacePathHit[] {
  const hits: WorkspacePathHit[] = [];
  PATH_INNER.lastIndex = 0;
  let m: RegExpExecArray | null;
  // biome-ignore lint/suspicious/noAssignInExpressions: idiomatic regex scan
  while ((m = PATH_INNER.exec(value)) !== null) {
    const path = m[0];
    if (!isWorkspaceFilePath(path)) continue;
    const start = m.index;
    if (start > 0) {
      const before = value[start - 1] ?? "";
      if (before === "/" || before === ":" || !BOUNDARY_BEFORE.test(before)) {
        continue;
      }
    }
    hits.push({ start, end: start + path.length, path });
  }
  return hits;
}

export type WorkspacePathPart =
  | { type: "text"; value: string }
  | { type: "path"; value: string };

export function splitWorkspacePathText(value: string): WorkspacePathPart[] {
  const hits = findWorkspaceFilePaths(value);
  if (hits.length === 0) return [{ type: "text", value }];
  const parts: WorkspacePathPart[] = [];
  let last = 0;
  for (const hit of hits) {
    if (hit.start < last) continue;
    if (hit.start > last) {
      parts.push({ type: "text", value: value.slice(last, hit.start) });
    }
    parts.push({ type: "path", value: hit.path });
    last = hit.end;
  }
  if (last < value.length) {
    parts.push({ type: "text", value: value.slice(last) });
  }
  return parts.length ? parts : [{ type: "text", value }];
}
