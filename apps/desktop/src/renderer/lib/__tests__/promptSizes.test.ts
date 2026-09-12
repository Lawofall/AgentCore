import {
  type PromptCatalogItem,
  type PromptRail,
  mineCatalogId,
  placeholderCatalogId,
} from "@/lib/promptCatalog";
import {
  buildAlwaysRows,
  formatAlwaysRowChars,
  formatPromptChars,
  promptBodyChars,
} from "@/lib/promptSizes";
import { describe, expect, it } from "vitest";

function emptyRail(over: Partial<PromptRail> = {}): PromptRail {
  return {
    constitution: [],
    memory: [],
    alwaysMine: [],
    folders: [],
    official: [],
    tools: [],
    ...over,
  };
}

function sharedItem(text: string): PromptCatalogItem {
  return {
    id: "shared",
    kind: "shared",
    group: "factory",
    label: "全员共享准则",
    depth: 0,
    text,
  };
}

function identityItem(
  ceoIdentity: string,
  nestedIdentity = "",
  leafIdentity = "",
): PromptCatalogItem {
  return {
    id: "identity",
    kind: "identity",
    group: "factory",
    label: "角色身份",
    depth: 0,
    ceoIdentity,
    nestedIdentity,
    leafIdentity,
  };
}

function mineItem(over: {
  id: string;
  label: string;
  content?: string;
  applyMode?: "always" | "on_demand";
  memoryKind?: "preferences" | "profile" | null;
  disputed?: boolean;
  alwaysChars?: number | null;
  parentId?: string | null;
}): Extract<PromptCatalogItem, { kind: "mine" }> {
  const content = over.content ?? "";
  const applyMode = over.applyMode ?? "always";
  const placeholder = over.id.startsWith("placeholder:");
  return {
    id: placeholder ? over.id : mineCatalogId(over.id),
    kind: "mine",
    group: "mine",
    label: over.label,
    depth: 0,
    mineId: placeholder ? "" : over.id,
    description: "",
    content,
    version: "v1",
    applyMode,
    aiMaintained: over.memoryKind != null,
    memoryKind: over.memoryKind ?? null,
    listable: applyMode === "on_demand",
    disputed: over.disputed ?? false,
    alwaysChars:
      over.alwaysChars !== undefined
        ? over.alwaysChars
        : applyMode === "always"
          ? promptBodyChars(content)
          : null,
    parentId: over.parentId ?? null,
  };
}

describe("buildAlwaysRows", () => {
  it("空核也进名单", () => {
    const rail = emptyRail({
      constitution: [sharedItem(""), identityItem("")],
      memory: [
        mineItem({
          id: placeholderCatalogId("preferences"),
          label: "偏好",
          content: "",
          memoryKind: "preferences",
        }),
      ],
    });
    expect(buildAlwaysRows(rail).map((row) => row.label)).toEqual([
      "全员共享准则",
      "角色身份",
      "偏好",
    ]);
  });

  it("停用的我的常驻仍在名单", () => {
    const rail = emptyRail({
      alwaysMine: [
        mineItem({
          id: "dead",
          label: "停用",
          content: "still-long-text",
          disputed: true,
        }),
      ],
    });
    expect(buildAlwaysRows(rail).map((row) => [row.label, row.meta])).toEqual([
      ["停用", "已停用，不再注入"],
    ]);
  });

  it("偏好画像缺介绍时用职责句", () => {
    const rows = buildAlwaysRows(
      emptyRail({
        memory: [
          mineItem({
            id: "pref",
            label: "偏好",
            memoryKind: "preferences",
            content: "x",
          }),
          mineItem({
            id: "prof",
            label: "画像",
            memoryKind: "profile",
            content: "y",
          }),
        ],
      }),
    );
    expect(rows.map((row) => [row.label, row.meta])).toEqual([
      ["偏好", "怎么回答"],
      ["画像", "关于用户"],
    ]);
  });

  it("角色身份简介用 CEO 身份正文", () => {
    const rows = buildAlwaysRows(
      emptyRail({
        constitution: [identityItem("你是团队的 CEO")],
      }),
    );
    expect(rows[0]?.meta).toBe("你是团队的 CEO");
  });
});

describe("formatPromptChars", () => {
  it("悬停用字数，不写百分比", () => {
    expect(formatPromptChars(12)).toBe("12 字");
    expect(formatPromptChars(12)).not.toMatch(/%/);
  });

  it("行尾不足千字不标", () => {
    expect(formatAlwaysRowChars(999)).toBeNull();
    expect(formatAlwaysRowChars(1000)).toBe("1000 字");
  });
});
