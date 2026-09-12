import {
  MAX_CHART_ITEMS,
  MAX_CHART_LABEL_CHARS,
  MAX_CHART_TITLE_CHARS,
  MAX_CHART_VALUE,
  MAX_TABLE_CELL_CHARS,
  MAX_TABLE_COLUMNS,
  MAX_TABLE_ROWS,
  createChartBlock,
  createTableBlock,
  parseDocBody,
} from "@/lib/docBody";
import { describe, expect, it } from "vitest";

describe("parseDocBody", () => {
  it("keeps heading paragraph list table chart and drops unknown types", () => {
    const body = parseDocBody({
      schemaVersion: 9,
      blocks: [
        { id: "h1", type: "heading", level: 2, text: "结论" },
        { id: "p1", type: "paragraph", text: "正文" },
        { id: "l1", type: "list", ordered: true, items: ["一"] },
        {
          id: "t1",
          type: "table",
          columns: ["A", "B"],
          rows: [["1", "2"]],
        },
        {
          id: "c1",
          type: "chart",
          title: "指标",
          items: [{ label: "A", value: 2 }],
        },
        { id: "bad", type: "chart", kind: "pie", items: [] },
        { id: "x", type: "widget", data: [] },
      ],
    });
    expect(body.schemaVersion).toBe(1);
    expect(body.blocks.map((b) => b.type)).toEqual([
      "heading",
      "paragraph",
      "list",
      "table",
      "chart",
    ]);
    expect(body.blocks[0]).toMatchObject({ level: 2, text: "结论" });
    expect(body.blocks[3]).toMatchObject({
      type: "table",
      columns: ["A", "B"],
      rows: [["1", "2"]],
    });
    expect(body.blocks[4]).toMatchObject({
      type: "chart",
      kind: "bar",
      title: "指标",
      items: [{ label: "A", value: 2 }],
    });
  });

  it("clamps table shape on parse", () => {
    const long = "x".repeat(MAX_TABLE_CELL_CHARS + 10);
    const body = parseDocBody({
      blocks: [
        {
          type: "table",
          columns: Array.from({ length: MAX_TABLE_COLUMNS + 2 }, (_, i) =>
            String(i),
          ),
          rows: Array.from({ length: MAX_TABLE_ROWS + 5 }, () => [long, "b"]),
        },
      ],
    });
    const table = body.blocks[0];
    expect(table?.type).toBe("table");
    if (table?.type !== "table") return;
    expect(table.columns).toHaveLength(MAX_TABLE_COLUMNS);
    expect(table.rows).toHaveLength(MAX_TABLE_ROWS);
    expect(table.rows[0][0]).toHaveLength(MAX_TABLE_CELL_CHARS);
  });

  it("createTableBlock defaults to two columns and three rows", () => {
    const block = createTableBlock();
    expect(block.columns).toEqual(["", ""]);
    expect(block.rows).toHaveLength(3);
    expect(block.rows.every((r) => r.length === 2)).toBe(true);
  });

  it("returns empty body for garbage", () => {
    expect(parseDocBody(null).blocks).toEqual([]);
    expect(parseDocBody("nope").blocks).toEqual([]);
  });

  it("clamps chart title labels values and item count on parse", () => {
    const longTitle = "t".repeat(MAX_CHART_TITLE_CHARS + 5);
    const longLabel = "l".repeat(MAX_CHART_LABEL_CHARS + 5);
    const body = parseDocBody({
      blocks: [
        {
          type: "chart",
          title: longTitle,
          items: [
            { label: longLabel, value: 1.5 },
            { label: "ok", value: -3 },
            { label: "nan", value: "nope" },
            { label: "big", value: MAX_CHART_VALUE * 2 },
            ...Array.from({ length: MAX_CHART_ITEMS + 3 }, (_, i) => ({
              label: String(i),
              value: i,
            })),
          ],
        },
      ],
    });
    const chart = body.blocks[0];
    expect(chart?.type).toBe("chart");
    if (chart?.type !== "chart") return;
    expect(chart.kind).toBe("bar");
    expect(chart.title).toHaveLength(MAX_CHART_TITLE_CHARS);
    expect(chart.items).toHaveLength(MAX_CHART_ITEMS);
    expect(chart.items[0].label).toHaveLength(MAX_CHART_LABEL_CHARS);
    expect(chart.items[0].value).toBe(1.5);
    expect(chart.items[1].value).toBe(0);
    expect(chart.items.some((it) => it.label === "nan")).toBe(false);
    expect(chart.items[2].value).toBe(MAX_CHART_VALUE);
    expect(chart.items[2].label).toBe("big");
  });

  it("defaults chart kind to bar and keeps empty items", () => {
    const body = parseDocBody({
      blocks: [{ type: "chart", items: [] }],
    });
    const chart = body.blocks[0];
    expect(chart).toMatchObject({
      type: "chart",
      kind: "bar",
      title: "",
      items: [],
    });
  });

  it("createChartBlock defaults to bar title empty and three zero points", () => {
    const block = createChartBlock();
    expect(block).toMatchObject({
      type: "chart",
      kind: "bar",
      title: "",
      items: [
        { label: "", value: 0 },
        { label: "", value: 0 },
        { label: "", value: 0 },
      ],
    });
  });
});
