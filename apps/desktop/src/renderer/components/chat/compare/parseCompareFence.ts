import { resolveWorkspaceImageRef } from "./isWorkspaceImageRef";

export interface ComparePaneSpec {
  /** 展示标签（alt / 标题行）。 */
  label: string;
  /** 可选槽位字母（A / B / …）。 */
  slot?: string;
  /** 已校验的工作区相对图片路径。 */
  path: string;
}

const MD_IMAGE = /^!\[([^\]]*)\]\(([^)]+)\)\s*$/;
const SLOT_LABEL = /^([A-Za-z])\s*[|:：]\s*(.+)$/;
const PANE_SEP = /^\s*---\s*$/m;

/**
 * 解析 ```compare 围栏正文为 ≥2 个合法工作区图片格。
 *
 * 每格支持：
 * - `![标签](path/to.png)`
 * - `A|标签` + 下一行 `path/to.png`
 * - 首行标签 + 下一行 `path/to.png`
 *
 * 格与格之间用独立一行的 `---` 分隔。任意外链 / 非图片路径的格整段作废。
 */
export function parseCompareFence(body: string): ComparePaneSpec[] | null {
  const text = body.trim();
  if (!text) return null;

  const chunks = text
    .split(PANE_SEP)
    .map((c) => c.trim())
    .filter(Boolean);
  if (chunks.length < 2) return null;

  const panes: ComparePaneSpec[] = [];
  for (const chunk of chunks) {
    const pane = parsePane(chunk);
    if (!pane) return null;
    panes.push(pane);
  }
  return panes.length >= 2 ? panes : null;
}

function parsePane(chunk: string): ComparePaneSpec | null {
  const lines = chunk
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length === 0) return null;

  const md = MD_IMAGE.exec(lines[0]);
  if (md) {
    const path = resolveWorkspaceImageRef(md[2]);
    if (!path) return null;
    return { label: md[1].trim() || path, path };
  }

  if (lines.length < 2) return null;
  const header = lines[0];
  const path = resolveWorkspaceImageRef(lines[1]);
  if (!path) return null;

  const slotMatch = SLOT_LABEL.exec(header);
  if (slotMatch) {
    return {
      slot: slotMatch[1].toUpperCase(),
      label: slotMatch[2].trim() || path,
      path,
    };
  }
  return { label: header || path, path };
}
