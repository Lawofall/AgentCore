import {
  type PromptCatalogItem,
  type PromptRail,
  mineCatalogId,
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
    alwaysMine: [],
    pathMine: [],
    folders: [],
    official: [],
    tools: [],
    ...over,
  };
}

function mineItem(over: {
  id: string;
  label: string;
  content?: string;
  applyMode?: "always" | "on_demand";
  aiMaintained?: boolean;
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
    aiMaintained: over.aiMaintained ?? false,
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
    expect(buildAlwaysRows(rail).map((row) => row.label)).toEqual(["停用"]);
    expect(buildAlwaysRows(rail)[0]?.item).toMatchObject({ disputed: true });
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
