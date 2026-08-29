import { describe, expect, it } from "vitest";
import {
  parseMentionFilter,
  pickOnDemandSettings,
  pickRecentConversations,
} from "../composerAttachments";
import {
  buildMentionCategoryRows,
  categoryHighlightIndex,
  mentionMenuKeyAction,
  showMentionCategoryLevel,
} from "../mentionMenuLevel";

describe("parseMentionFilter", () => {
  it("returns null section when no type prefix", () => {
    expect(parseMentionFilter("readme")).toEqual({
      section: null,
      filter: "readme",
    });
    expect(parseMentionFilter("")).toEqual({ section: null, filter: "" });
  });

  it("strips Chinese type prefixes", () => {
    expect(parseMentionFilter("团队")).toEqual({
      section: "team",
      filter: "",
    });
    expect(parseMentionFilter("对话 foo")).toEqual({
      section: "conversation",
      filter: "foo",
    });
    expect(parseMentionFilter("文件夹 src")).toEqual({
      section: "folder",
      filter: "src",
    });
    expect(parseMentionFilter("文件 a.ts")).toEqual({
      section: "file",
      filter: "a.ts",
    });
    expect(parseMentionFilter("设定 简短")).toEqual({
      section: "setting",
      filter: "简短",
    });
  });

  it("strips English prefixes case-insensitively", () => {
    expect(parseMentionFilter("Agent")).toEqual({
      section: "team",
      filter: "",
    });
    expect(parseMentionFilter("conv hello")).toEqual({
      section: "conversation",
      filter: "hello",
    });
    expect(parseMentionFilter("DIR lib")).toEqual({
      section: "folder",
      filter: "lib",
    });
    expect(parseMentionFilter("file x")).toEqual({
      section: "file",
      filter: "x",
    });
    expect(parseMentionFilter("note foo")).toEqual({
      section: "setting",
      filter: "foo",
    });
  });

  it("prefers 文件夹 over 文件", () => {
    expect(parseMentionFilter("文件夹")).toEqual({
      section: "folder",
      filter: "",
    });
  });
});

describe("pickRecentConversations", () => {
  const list = [
    { id: "c1", title: "当前会话" },
    { id: "c2", title: "昨日讨论" },
    { id: "c3", title: "设计评审" },
    { id: "c4", title: "无关" },
  ];

  it("excludes current conversation and limits", () => {
    const items = pickRecentConversations(list, "c1", "", 2);
    expect(items.map((i) => i.relPath)).toEqual(["c2", "c3"]);
    expect(items.every((i) => i.kind === "conversation")).toBe(true);
  });

  it("filters by title substring", () => {
    const items = pickRecentConversations(list, null, "设计");
    expect(items.map((i) => i.relPath)).toEqual(["c3"]);
  });
});

describe("showMentionCategoryLevel", () => {
  it("shows L1 only when empty and not drilled", () => {
    expect(
      showMentionCategoryLevel({
        sectionFilter: null,
        activeCategory: null,
        filterText: "",
      }),
    ).toBe(true);
    expect(
      showMentionCategoryLevel({
        sectionFilter: null,
        activeCategory: null,
        filterText: "  ",
      }),
    ).toBe(true);
  });

  it("hides L1 when typed, prefixed, or drilled", () => {
    expect(
      showMentionCategoryLevel({
        sectionFilter: null,
        activeCategory: null,
        filterText: "readme",
      }),
    ).toBe(false);
    expect(
      showMentionCategoryLevel({
        sectionFilter: "conversation",
        activeCategory: null,
        filterText: "",
      }),
    ).toBe(false);
    expect(
      showMentionCategoryLevel({
        sectionFilter: null,
        activeCategory: "file",
        filterText: "",
      }),
    ).toBe(false);
  });
});

describe("buildMentionCategoryRows", () => {
  it("附件置顶，文件/文件夹前移，空团队整档隐藏", () => {
    const rows = buildMentionCategoryRows({
      counts: { team: 0, conversation: 3, folder: 0, file: 12, setting: 0 },
    });
    expect(rows.map((r) => r.id)).toEqual([
      "attach",
      "file",
      "folder",
      "conversation",
    ]);
    expect(rows[0]).toMatchObject({
      id: "attach",
      label: "附件",
      hint: "从本机添加",
      disabled: false,
    });
    expect(rows.find((r) => r.id === "team")).toBeUndefined();
    expect(rows[1]).toMatchObject({ id: "file", count: 12, disabled: false });
    expect(rows[2]).toMatchObject({ id: "folder", count: 0, disabled: false });
    expect(rows[3]).toMatchObject({
      id: "conversation",
      count: 3,
      disabled: false,
    });
  });

  it("有团队时沉到最后，不置灰", () => {
    const rows = buildMentionCategoryRows({
      counts: { team: 2, conversation: 1, folder: 4, file: 8, setting: 0 },
    });
    expect(rows.map((r) => r.id)).toEqual([
      "attach",
      "file",
      "folder",
      "conversation",
      "team",
    ]);
    expect(rows[4]).toMatchObject({ id: "team", count: 2, disabled: false });
  });

  it("有按需设定时插在对话和团队之间", () => {
    const rows = buildMentionCategoryRows({
      counts: { team: 1, conversation: 1, folder: 0, file: 0, setting: 2 },
    });
    expect(rows.map((r) => r.id)).toEqual([
      "attach",
      "file",
      "folder",
      "conversation",
      "setting",
      "team",
    ]);
  });

  it("marks file/folder loading when index is still empty", () => {
    const rows = buildMentionCategoryRows({
      counts: { team: 1, conversation: 0, folder: 0, file: 0, setting: 0 },
      loadingFiles: true,
    });
    expect(rows[1]).toMatchObject({ id: "file", loading: true });
    expect(rows[2]).toMatchObject({ id: "folder", loading: true });
    expect(rows[4]).toMatchObject({ id: "team", disabled: false });
  });
});

describe("pickOnDemandSettings", () => {
  const list = [
    {
      id: "d1",
      name: "说话简短",
      description: "回复短句",
      applyMode: "on_demand",
      disputedAt: null,
      frontmatterError: null,
      kind: "document",
    },
    {
      id: "d2",
      name: "常驻偏好",
      description: "",
      applyMode: "always",
      disputedAt: null,
      frontmatterError: null,
      kind: "document",
    },
    {
      id: "d3",
      name: "已停用",
      description: "旧规则",
      applyMode: "on_demand",
      disputedAt: "2026-01-01",
      frontmatterError: null,
      kind: "document",
    },
  ];

  it("只列出可点名的按需条目", () => {
    const items = pickOnDemandSettings(list, "", 10);
    expect(items.map((i) => i.relPath)).toEqual(["d1"]);
    expect(items[0]?.kind).toBe("document");
  });

  it("按名字或摘要过滤", () => {
    const items = pickOnDemandSettings(list, "短句");
    expect(items.map((i) => i.relPath)).toEqual(["d1"]);
  });
});

describe("categoryHighlightIndex", () => {
  const rows = [
    { id: "attach" },
    { id: "file" },
    { id: "folder" },
    { id: "conversation" },
  ];

  it("hits the preferred row when present", () => {
    expect(categoryHighlightIndex(rows, "attach")).toBe(0);
    expect(categoryHighlightIndex(rows, "file")).toBe(1);
  });

  it("falls back to file when team is hidden", () => {
    expect(categoryHighlightIndex(rows, "team")).toBe(1);
  });
});

describe("mentionMenuKeyAction", () => {
  const l1 = {
    showCategoryLevel: true,
    categoryCount: 5,
    activeIndex: 1,
    categoryDisabled: true,
    categoryAttach: false,
    itemCount: 0,
    canKeyBack: false,
  };

  it("navigates and drills on L1; Enter on disabled team is ignored", () => {
    expect(mentionMenuKeyAction("ArrowDown", l1)).toEqual({
      type: "move",
      index: 2,
    });
    expect(mentionMenuKeyAction("Enter", l1)).toEqual({ type: "ignore" });
    expect(mentionMenuKeyAction("Tab", l1)).toEqual({ type: "ignore" });
    expect(mentionMenuKeyAction("ArrowRight", l1)).toEqual({ type: "consume" });
    expect(
      mentionMenuKeyAction("Enter", {
        ...l1,
        activeIndex: 2,
        categoryDisabled: false,
      }),
    ).toEqual({ type: "drill" });
    expect(
      mentionMenuKeyAction("ArrowRight", {
        ...l1,
        activeIndex: 2,
        categoryDisabled: false,
      }),
    ).toEqual({ type: "drill" });
  });

  it("附件行：Enter 选文件，ArrowRight 不 drill", () => {
    const attach = {
      ...l1,
      activeIndex: 0,
      categoryDisabled: false,
      categoryAttach: true,
    };
    expect(mentionMenuKeyAction("Enter", attach)).toEqual({ type: "attach" });
    expect(mentionMenuKeyAction("Tab", attach)).toEqual({ type: "attach" });
    expect(mentionMenuKeyAction("ArrowRight", attach)).toEqual({
      type: "consume",
    });
  });

  it("goes back on L2 with empty filter; leaves ArrowLeft through when typing", () => {
    const l2 = {
      showCategoryLevel: false,
      categoryCount: 5,
      activeIndex: 0,
      categoryDisabled: false,
      categoryAttach: false,
      itemCount: 3,
      canKeyBack: true,
    };
    expect(mentionMenuKeyAction("ArrowLeft", l2)).toEqual({ type: "back" });
    expect(mentionMenuKeyAction("Enter", l2)).toEqual({ type: "select" });
    expect(
      mentionMenuKeyAction("ArrowLeft", { ...l2, canKeyBack: false }),
    ).toEqual({
      type: "ignore",
    });
  });

  it("closes on Escape from either level", () => {
    expect(mentionMenuKeyAction("Escape", l1)).toEqual({ type: "close" });
  });
});
