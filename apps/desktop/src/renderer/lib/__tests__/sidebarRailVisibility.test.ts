import {
  BARE_LIMIT_SOLO,
  BARE_LIMIT_WITH_GROUPS,
  MAX_GROUP_VISIBLE,
  MAX_PER_GROUP,
  conversationAtRailHotkey,
  isGroupExpanded,
  listVisibleRailConversations,
  pickBareVisible,
  pickGroupVisible,
  railHotkeySlots,
} from "@/lib/sidebarRailVisibility";
import type { Conversation } from "@/stores/conversation";
import { describe, expect, it } from "vitest";

const conv = (
  id: string,
  opts: {
    at?: string;
    archived?: boolean;
    pinned?: boolean;
    folderId?: string | null;
  } = {},
): Conversation => ({
  id,
  title: id,
  updatedAt: opts.at ?? "2026-01-01T00:00:00Z",
  messageCount: 0,
  lastMessagePreview: null,
  archived: opts.archived,
  pinned: opts.pinned,
  folderId: opts.folderId,
});

function ids(list: Conversation[]): string[] {
  return list.map((c) => c.id);
}

describe("pickGroupVisible", () => {
  const rows = [
    conv("n1", { at: "2026-06-06T00:00:00Z" }),
    conv("n2", { at: "2026-06-05T00:00:00Z" }),
    conv("n3", { at: "2026-06-04T00:00:00Z" }),
    conv("n4", { at: "2026-06-03T00:00:00Z" }),
    conv("n5", { at: "2026-06-02T00:00:00Z" }),
    conv("old-req", { at: "2026-01-01T00:00:00Z" }),
    conv("older", { at: "2025-12-01T00:00:00Z" }),
  ];

  it("required 已在 Top 5 时不重复、不加长", () => {
    expect(ids(pickGroupVisible(rows, new Set(["n2"])))).toEqual([
      "n1",
      "n2",
      "n3",
      "n4",
      "n5",
    ]);
  });

  it("无 required 仍 Top 5", () => {
    expect(ids(pickGroupVisible(rows, new Set()))).toEqual([
      "n1",
      "n2",
      "n3",
      "n4",
      "n5",
    ]);
    expect(MAX_PER_GROUP).toBe(5);
  });

  it("帽外 required 挤进且总数 ≤6，保持 recency 序", () => {
    const shown = pickGroupVisible(rows, new Set(["old-req"]));
    expect(ids(shown)).toEqual(["n1", "n2", "n3", "n4", "n5", "old-req"]);
    expect(shown).toHaveLength(MAX_GROUP_VISIBLE);
  });

  it("多个 required 优先于普通行，仍 ≤6", () => {
    const many = [
      ...rows,
      conv("req-a", { at: "2026-02-01T00:00:00Z" }),
      conv("req-b", { at: "2026-02-02T00:00:00Z" }),
    ];
    const shown = pickGroupVisible(
      many,
      new Set(["old-req", "req-a", "req-b"]),
    );
    expect(ids(shown)).toContain("old-req");
    expect(ids(shown)).toContain("req-a");
    expect(ids(shown)).toContain("req-b");
    expect(shown.length).toBeLessThanOrEqual(MAX_GROUP_VISIBLE);
  });

  it("归档 required 不拉回组内", () => {
    const withArchived = [
      ...rows.slice(0, 5),
      conv("arch-req", { at: "2026-01-01T00:00:00Z", archived: true }),
    ];
    expect(ids(pickGroupVisible(withArchived, new Set(["arch-req"])))).toEqual([
      "n1",
      "n2",
      "n3",
      "n4",
      "n5",
    ]);
  });
});

describe("pickBareVisible", () => {
  const bare = Array.from({ length: 16 }, (_, i) =>
    conv(`b${i}`, {
      at: `2026-06-${String(16 - i).padStart(2, "0")}T00:00:00Z`,
    }),
  );

  it("帽外 required 像 currentId 一样回塞", () => {
    const shown = pickBareVisible(bare, {
      limit: BARE_LIMIT_WITH_GROUPS,
      currentId: null,
      requiredIds: new Set(["b15"]),
    });
    expect(ids(shown).slice(0, BARE_LIMIT_WITH_GROUPS)).toEqual(
      bare.slice(0, BARE_LIMIT_WITH_GROUPS).map((c) => c.id),
    );
    expect(ids(shown)).toContain("b15");
  });

  it("currentId 与 required 都回塞", () => {
    const shown = pickBareVisible(bare, {
      limit: BARE_LIMIT_SOLO,
      currentId: "b15",
      requiredIds: new Set(["b14"]),
    });
    expect(ids(shown)).toContain("b15");
    expect(ids(shown)).toContain("b14");
  });

  it("归档 required 不拉回裸聊区", () => {
    const list = [
      ...bare.slice(0, 3),
      conv("arch", { archived: true, at: "2020-01-01T00:00:00Z" }),
    ];
    const shown = pickBareVisible(list, {
      limit: 2,
      currentId: null,
      requiredIds: new Set(["arch"]),
    });
    expect(ids(shown)).toEqual(["b0", "b1"]);
  });
});

describe("isGroupExpanded", () => {
  it("required 期间盖过 persist 折叠", () => {
    expect(
      isGroupExpanded({
        stored: false,
        isActiveFolder: false,
        hasRequired: true,
      }),
    ).toBe(true);
  });

  it("required 消失后回到 persist 折叠", () => {
    expect(
      isGroupExpanded({
        stored: false,
        isActiveFolder: false,
        hasRequired: false,
      }),
    ).toBe(false);
  });

  it("无 persist 时当前对话所在组仍默认展开", () => {
    expect(
      isGroupExpanded({
        stored: undefined,
        isActiveFolder: true,
        hasRequired: false,
      }),
    ).toBe(true);
  });
});

describe("listVisibleRailConversations", () => {
  const pin = conv("pin", {
    pinned: true,
    at: "2026-08-01T00:00:00Z",
    folderId: "f1",
  });
  const inGroup = conv("g1", {
    folderId: "f1",
    at: "2026-07-01T00:00:00Z",
  });
  const bare = conv("bare", { at: "2026-06-01T00:00:00Z" });
  const owned = [{ folder: { id: "f1" }, convs: [pin, inGroup] }];

  it("置顶在前，折叠组内不计，裸聊在后", () => {
    expect(
      ids(
        listVisibleRailConversations({
          conversations: [bare, inGroup, pin],
          ownedGroups: owned,
          sharedGroups: [],
          expandedSections: {},
          currentId: null,
          requiredIds: new Set(),
        }),
      ),
    ).toEqual(["pin", "bare"]);
  });

  it("展开组才计入组内未置顶行", () => {
    expect(
      ids(
        listVisibleRailConversations({
          conversations: [bare, inGroup, pin],
          ownedGroups: owned,
          sharedGroups: [],
          expandedSections: { f1: true },
          currentId: null,
          requiredIds: new Set(),
        }),
      ),
    ).toEqual(["pin", "g1", "bare"]);
  });

  it("当前对话所在组无 persist 时默认展开", () => {
    expect(
      ids(
        listVisibleRailConversations({
          conversations: [bare, inGroup, pin],
          ownedGroups: owned,
          sharedGroups: [],
          expandedSections: {},
          currentId: "g1",
          requiredIds: new Set(),
        }),
      ),
    ).toEqual(["pin", "g1", "bare"]);
  });

  it("共享组排在自有组之后", () => {
    const sharedChat = conv("shared", {
      folderId: "sf",
      at: "2026-09-01T00:00:00Z",
    });
    expect(
      ids(
        listVisibleRailConversations({
          conversations: [bare, inGroup, pin, sharedChat],
          ownedGroups: owned,
          sharedGroups: [{ folder: { id: "sf" }, convs: [sharedChat] }],
          expandedSections: { f1: true, sf: true },
          currentId: null,
          requiredIds: new Set(),
        }),
      ),
    ).toEqual(["pin", "g1", "shared", "bare"]);
  });
});

describe("railHotkeySlots / conversationAtRailHotkey", () => {
  const rows = Array.from({ length: 12 }, (_, i) => conv(`c${i}`));

  it("只给前 9 行编号", () => {
    const slots = railHotkeySlots(rows);
    expect(slots.get("c0")).toBe(1);
    expect(slots.get("c8")).toBe(9);
    expect(slots.get("c9")).toBeUndefined();
  });

  it("空槽 / 非法键不命中", () => {
    expect(conversationAtRailHotkey("1", rows)?.id).toBe("c0");
    expect(conversationAtRailHotkey("9", rows)?.id).toBe("c8");
    expect(conversationAtRailHotkey("1", [])).toBeUndefined();
    expect(conversationAtRailHotkey("0", rows)).toBeUndefined();
    expect(conversationAtRailHotkey("a", rows)).toBeUndefined();
  });
});
