import type { PromptCatalogItem, PromptRail } from "@/lib/promptCatalog";
import {
  EMPTY_MINE_SELECTION,
  type MineSelectedItem,
  type MineSelection,
  clickIntent,
  dropFromSelection,
  flattenVisibleMineItems,
  selectRow,
  selectionForContextMenu,
} from "@/lib/promptCatalogSelection";
import { describe, expect, it } from "vitest";

function mine(
  id: string,
  label: string,
  applyMode: "always" | "on_demand" = "on_demand",
  parentId: string | null = "f1",
): Extract<PromptCatalogItem, { kind: "mine" }> {
  return {
    id: `mine:${id}`,
    kind: "mine",
    group: "mine",
    label,
    depth: 0,
    mineId: id,
    description: `${label}介绍`,
    content: "HOW",
    version: "v1",
    applyMode,
    aiMaintained: false,
    listable: true,
    disputed: false,
    alwaysChars: applyMode === "always" ? 12 : null,
    parentId: applyMode === "always" ? null : parentId,
  };
}

function item(id: string, label: string): MineSelectedItem {
  return { catalogId: `mine:${id}`, mineId: id, label };
}

const VISIBLE: MineSelectedItem[] = [
  item("a", "甲"),
  item("b", "乙"),
  item("c", "丙"),
  item("d", "丁"),
];

function ids(sel: MineSelection): string[] {
  return sel.items.map((i) => i.mineId);
}

const PLAIN = { toggle: false, range: false };
const TOGGLE = { toggle: true, range: false };
const RANGE = { toggle: false, range: true };

describe("clickIntent", () => {
  it("Ctrl 与 Cmd 都是加减选，Shift 是连选", () => {
    expect(
      clickIntent({ ctrlKey: true, metaKey: false, shiftKey: false }),
    ).toEqual({ toggle: true, range: false });
    expect(
      clickIntent({ ctrlKey: false, metaKey: true, shiftKey: false }),
    ).toEqual({ toggle: true, range: false });
    expect(
      clickIntent({ ctrlKey: false, metaKey: false, shiftKey: true }),
    ).toEqual({ toggle: false, range: true });
  });
});

describe("selectRow", () => {
  it("普通点击 = 单选并把锚点挪过来", () => {
    const sel = selectRow(
      EMPTY_MINE_SELECTION,
      item("c", "丙"),
      PLAIN,
      VISIBLE,
    );
    expect(ids(sel)).toEqual(["c"]);
    expect(sel.anchor).toBe("mine:c");
  });

  it("Ctrl 点击加减，选区按可见顺序排", () => {
    let sel = selectRow(EMPTY_MINE_SELECTION, item("d", "丁"), PLAIN, VISIBLE);
    sel = selectRow(sel, item("a", "甲"), TOGGLE, VISIBLE);
    expect(ids(sel)).toEqual(["a", "d"]);
    sel = selectRow(sel, item("d", "丁"), TOGGLE, VISIBLE);
    expect(ids(sel)).toEqual(["a"]);
  });

  it("Shift 从锚点连选，锚点已不可见时退化成单选", () => {
    const anchored = selectRow(
      EMPTY_MINE_SELECTION,
      item("a", "甲"),
      PLAIN,
      VISIBLE,
    );
    const down = selectRow(anchored, item("c", "丙"), RANGE, VISIBLE);
    expect(ids(down)).toEqual(["a", "b", "c"]);
    expect(down.anchor).toBe("mine:a");

    const stale: MineSelection = {
      items: [item("gone", "没了")],
      anchor: "mine:gone",
    };
    const fallback = selectRow(stale, item("c", "丙"), RANGE, VISIBLE);
    expect(ids(fallback)).toEqual(["c"]);
  });
});

describe("selectionForContextMenu", () => {
  it("右键点在选区内保持整批，点在选区外收敛成单选", () => {
    const multi: MineSelection = {
      items: [item("a", "甲"), item("b", "乙")],
      anchor: "mine:b",
    };
    expect(ids(selectionForContextMenu(multi, item("a", "甲")))).toEqual([
      "a",
      "b",
    ]);
    expect(ids(selectionForContextMenu(multi, item("d", "丁")))).toEqual(["d"]);
  });
});

describe("dropFromSelection", () => {
  const sel: MineSelection = {
    items: [item("a", "甲"), item("b", "乙")],
    anchor: "mine:b",
  };

  it("删掉的行从选区摘掉，锚点跟着作废", () => {
    const next = dropFromSelection(sel, ["mine:b"]);
    expect(ids(next)).toEqual(["a"]);
    expect(next.anchor).toBeNull();
  });

  it("没命中时返回原选区", () => {
    expect(dropFromSelection(sel, ["mine:z"])).toBe(sel);
  });
});

describe("flattenVisibleMineItems", () => {
  const rail: PromptRail = {
    constitution: [
      {
        id: "shared",
        kind: "shared",
        group: "factory",
        label: "全员共享准则",
        depth: 0,
        text: "核",
      },
    ],
    alwaysMine: [mine("pin", "置顶", "always", null)],
    pathMine: [],
    folders: [
      {
        id: "folder:law",
        name: "法律",
        source: "user",
        documentId: "f1",
        items: [mine("x", "合同"), mine("y", "尽调")],
      },
      {
        id: "folder:other",
        name: "其他",
        source: "other",
        documentId: "f2",
        items: [mine("z", "随手", "on_demand", "f2")],
      },
    ],
    official: [],
    tools: [],
  };

  it("根上是必带和散落按需；夹里的卡要有搜索词才算可见", () => {
    expect(flattenVisibleMineItems(rail).map((i) => i.mineId)).toEqual([
      "pin",
      "z",
    ]);
  });

  it("搜索命中夹里的条目时把它铺出来；只命中夹名不把里面拉出来", () => {
    expect(flattenVisibleMineItems(rail, "合同").map((i) => i.mineId)).toEqual([
      "x",
    ]);
    expect(flattenVisibleMineItems(rail, "法律").map((i) => i.mineId)).toEqual(
      [],
    );
  });

  it("点进夹只看见这只夹", () => {
    expect(
      flattenVisibleMineItems(rail, "", "folder:law").map((i) => i.mineId),
    ).toEqual(["x", "y"]);
  });
});
