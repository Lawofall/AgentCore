/** Block-list body for the creation-tool 文档 (mirrors server `agentcore.doc.body`). */

export const DOC_BODY_SCHEMA_VERSION = 1;

export const MAX_TABLE_COLUMNS = 12;
export const MAX_TABLE_ROWS = 40;
export const MAX_TABLE_CELL_CHARS = 500;

export const MAX_CHART_ITEMS = 12;
export const MAX_CHART_TITLE_CHARS = 200;
export const MAX_CHART_LABEL_CHARS = 80;
export const MAX_CHART_VALUE = 1e12;

export type DocChartItem = {
  label: string;
  value: number;
};

export type DocChartBlock = {
  id: string;
  type: "chart";
  kind: "bar";
  title: string;
  items: DocChartItem[];
};

export type DocHeadingBlock = {
  id: string;
  type: "heading";
  level: 1 | 2 | 3;
  text: string;
};

export type DocParagraphBlock = {
  id: string;
  type: "paragraph";
  text: string;
};

export type DocListBlock = {
  id: string;
  type: "list";
  ordered: boolean;
  items: string[];
};

export type DocTableBlock = {
  id: string;
  type: "table";
  columns: string[];
  rows: string[][];
};

export type DocBlock =
  | DocHeadingBlock
  | DocParagraphBlock
  | DocListBlock
  | DocTableBlock
  | DocChartBlock;

export type DocBody = {
  schemaVersion: number;
  blocks: DocBlock[];
};

export function emptyDocBody(): DocBody {
  return { schemaVersion: DOC_BODY_SCHEMA_VERSION, blocks: [] };
}

export function newBlockId(): string {
  return crypto.randomUUID();
}

export function clipTableCell(value: string): string {
  if (value.length <= MAX_TABLE_CELL_CHARS) return value;
  return value.slice(0, MAX_TABLE_CELL_CHARS);
}

export function parseDocBody(raw: unknown): DocBody {
  if (!raw || typeof raw !== "object") return emptyDocBody();
  const src = raw as { blocks?: unknown };
  if (!Array.isArray(src.blocks)) return emptyDocBody();
  const blocks: DocBlock[] = [];
  for (const item of src.blocks) {
    const block = parseBlock(item);
    if (block) blocks.push(block);
  }
  return { schemaVersion: DOC_BODY_SCHEMA_VERSION, blocks };
}

function parseBlock(item: unknown): DocBlock | null {
  if (!item || typeof item !== "object") return null;
  const row = item as Record<string, unknown>;
  const id = typeof row.id === "string" && row.id ? row.id : newBlockId();
  if (row.type === "heading") {
    const level = row.level === 2 || row.level === 3 ? row.level : 1;
    return { id, type: "heading", level, text: String(row.text ?? "") };
  }
  if (row.type === "paragraph") {
    return { id, type: "paragraph", text: String(row.text ?? "") };
  }
  if (row.type === "list") {
    const items = Array.isArray(row.items)
      ? row.items.map((x) => String(x ?? ""))
      : [];
    return { id, type: "list", ordered: Boolean(row.ordered), items };
  }
  if (row.type === "table") {
    return parseTableBlock(id, row);
  }
  if (row.type === "chart") {
    return parseChartBlock(id, row);
  }
  return null;
}

function clipChartTitle(value: string): string {
  if (value.length <= MAX_CHART_TITLE_CHARS) return value;
  return value.slice(0, MAX_CHART_TITLE_CHARS);
}

function clipChartLabel(value: string): string {
  if (value.length <= MAX_CHART_LABEL_CHARS) return value;
  return value.slice(0, MAX_CHART_LABEL_CHARS);
}

function parseChartValue(value: unknown): number | null {
  if (typeof value === "boolean") return null;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return null;
  if (n < 0) return 0;
  return Math.min(n, MAX_CHART_VALUE);
}

function parseChartBlock(
  id: string,
  row: Record<string, unknown>,
): DocChartBlock | null {
  const kindRaw = row.kind;
  if (kindRaw != null && kindRaw !== "" && kindRaw !== "bar") {
    return null;
  }
  const title = clipChartTitle(String(row.title ?? ""));
  const itemsRaw = Array.isArray(row.items) ? row.items : [];
  const items: DocChartItem[] = [];
  for (const entry of itemsRaw) {
    if (items.length >= MAX_CHART_ITEMS) break;
    if (!entry || typeof entry !== "object") continue;
    const item = entry as Record<string, unknown>;
    const value = parseChartValue(item.value);
    if (value === null) continue;
    items.push({
      label: clipChartLabel(String(item.label ?? "")),
      value,
    });
  }
  return { id, type: "chart", kind: "bar", title, items };
}

function parseTableBlock(
  id: string,
  row: Record<string, unknown>,
): DocTableBlock {
  const columnsRaw = Array.isArray(row.columns) ? row.columns : [];
  const columns = columnsRaw
    .slice(0, MAX_TABLE_COLUMNS)
    .map((x) => clipTableCell(String(x ?? "")));
  if (columns.length === 0) {
    columns.push("", "");
  }

  const colCount = columns.length;
  const rowsRaw = Array.isArray(row.rows) ? row.rows : [];
  const rows: string[][] = [];
  for (const entry of rowsRaw.slice(0, MAX_TABLE_ROWS)) {
    if (!Array.isArray(entry)) continue;
    const cells = entry
      .slice(0, colCount)
      .map((x) => clipTableCell(String(x ?? "")));
    while (cells.length < colCount) cells.push("");
    rows.push(cells);
  }
  if (rows.length === 0) {
    rows.push(Array.from({ length: colCount }, () => ""));
  }

  return { id, type: "table", columns, rows };
}

export function createHeadingBlock(level: 1 | 2 | 3 = 1): DocHeadingBlock {
  return { id: newBlockId(), type: "heading", level, text: "" };
}

export function createParagraphBlock(): DocParagraphBlock {
  return { id: newBlockId(), type: "paragraph", text: "" };
}

export function createListBlock(ordered = false): DocListBlock {
  return { id: newBlockId(), type: "list", ordered, items: [""] };
}

export function createTableBlock(): DocTableBlock {
  const columns = ["", ""];
  const blankRow = () => ["", ""];
  return {
    id: newBlockId(),
    type: "table",
    columns,
    rows: [blankRow(), blankRow(), blankRow()],
  };
}

export function createChartBlock(): DocChartBlock {
  const blank = (): DocChartItem => ({ label: "", value: 0 });
  return {
    id: newBlockId(),
    type: "chart",
    kind: "bar",
    title: "",
    items: [blank(), blank(), blank()],
  };
}

export function chartWithTitle(
  block: DocChartBlock,
  title: string,
): DocChartBlock {
  return { ...block, title: clipChartTitle(title) };
}

export function chartWithItemLabel(
  block: DocChartBlock,
  index: number,
  label: string,
): DocChartBlock {
  if (index < 0 || index >= block.items.length) return block;
  const items = block.items.map((it, i) =>
    i === index ? { ...it, label: clipChartLabel(label) } : it,
  );
  return { ...block, items };
}

export function chartWithItemValue(
  block: DocChartBlock,
  index: number,
  value: unknown,
): DocChartBlock {
  if (index < 0 || index >= block.items.length) return block;
  const next = parseChartValue(value) ?? 0;
  const items = block.items.map((it, i) =>
    i === index ? { ...it, value: next } : it,
  );
  return { ...block, items };
}

export function chartWithAddedItem(block: DocChartBlock): DocChartBlock | null {
  if (block.items.length >= MAX_CHART_ITEMS) return null;
  return {
    ...block,
    items: [...block.items, { label: "", value: 0 }],
  };
}

export function chartWithRemovedItem(
  block: DocChartBlock,
): DocChartBlock | null {
  if (block.items.length <= 1) return null;
  return { ...block, items: block.items.slice(0, -1) };
}

export function tableWithAddedColumn(
  block: DocTableBlock,
): DocTableBlock | null {
  if (block.columns.length >= MAX_TABLE_COLUMNS) return null;
  return {
    ...block,
    columns: [...block.columns, ""],
    rows: block.rows.map((r) => [...r, ""]),
  };
}

export function tableWithRemovedColumn(
  block: DocTableBlock,
): DocTableBlock | null {
  if (block.columns.length <= 1) return null;
  const nextCols = block.columns.slice(0, -1);
  return {
    ...block,
    columns: nextCols,
    rows: block.rows.map((r) => r.slice(0, -1)),
  };
}

export function tableWithAddedRow(block: DocTableBlock): DocTableBlock | null {
  if (block.rows.length >= MAX_TABLE_ROWS) return null;
  const blank = block.columns.map(() => "");
  return { ...block, rows: [...block.rows, blank] };
}

export function tableWithRemovedRow(
  block: DocTableBlock,
): DocTableBlock | null {
  if (block.rows.length <= 1) return null;
  return { ...block, rows: block.rows.slice(0, -1) };
}

export function tableWithHeaderCell(
  block: DocTableBlock,
  colIndex: number,
  value: string,
): DocTableBlock {
  if (colIndex < 0 || colIndex >= block.columns.length) return block;
  const columns = [...block.columns];
  columns[colIndex] = clipTableCell(value);
  return { ...block, columns };
}

export function tableWithCell(
  block: DocTableBlock,
  rowIndex: number,
  colIndex: number,
  value: string,
): DocTableBlock {
  if (rowIndex < 0 || rowIndex >= block.rows.length) return block;
  if (colIndex < 0 || colIndex >= block.columns.length) return block;
  const text = clipTableCell(value);
  const rows = block.rows.map((r, ri) =>
    ri === rowIndex ? r.map((c, ci) => (ci === colIndex ? text : c)) : r,
  );
  return { ...block, rows };
}
