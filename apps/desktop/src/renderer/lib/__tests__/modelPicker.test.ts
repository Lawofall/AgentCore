import {
  type DefaultProviderGroup,
  PLATFORM_POINTER_ID,
} from "@/lib/llmDefaults";
import {
  byokHandFillGroups,
  clusterSettingsRows,
  composerShelfLayout,
  emptyByokGroups,
  flattenPickerRows,
  isPickerEnabled,
  settingsChannelGroups,
  settingsChannelStartsCollapsed,
} from "@/lib/modelPicker";
import { describe, expect, it } from "vitest";

function opt(
  model: string,
  label: string,
  extra: Partial<DefaultProviderGroup["models"][number]> = {},
): DefaultProviderGroup["models"][number] {
  return { model, label, available: true, ...extra };
}

function groupsFixture(): DefaultProviderGroup[] {
  return [
    {
      providerId: PLATFORM_POINTER_ID,
      providerLabel: "平台",
      models: [opt("flash-free", "Flash Free", { origin: "platform" })],
    },
    {
      providerId: "p1",
      providerLabel: "DeepSeek",
      models: [opt("deepseek-v4.1-flash", "DeepSeek V4.1 Flash")],
    },
    {
      providerId: "p2",
      providerLabel: "OpenAI",
      models: [opt("gpt-4o", "GPT-4o")],
    },
  ];
}

const ALL_BYOK = ["@byok/p1/deepseek-v4.1-flash", "@byok/p2/gpt-4o"];

describe("isPickerEnabled", () => {
  it("keeps platform on unless hidden, and BYOK off unless opened", () => {
    expect(isPickerEnabled("@platform/flash-free", "platform")).toBe(true);
    expect(
      isPickerEnabled("@platform/flash-free", "platform", {
        hiddenRefs: ["@platform/flash-free"],
      }),
    ).toBe(false);
    expect(isPickerEnabled("@byok/p2/gpt-4o", "byok")).toBe(false);
    expect(
      isPickerEnabled("@byok/p2/gpt-4o", "byok", {
        enabledRefs: ["@byok/p2/gpt-4o"],
      }),
    ).toBe(true);
  });
});

describe("flattenPickerRows", () => {
  it("lists every channel in one list without moving the current row", () => {
    const selected = flattenPickerRows(groupsFixture(), {
      currentPointer: "@byok/p2/gpt-4o",
      enabledRefs: ALL_BYOK,
    });
    const other = flattenPickerRows(groupsFixture(), {
      currentPointer: "@platform/flash-free",
      enabledRefs: ALL_BYOK,
    });
    const order = [
      "@byok/p1/deepseek-v4.1-flash",
      "@platform/flash-free",
      "@byok/p2/gpt-4o",
    ];
    expect(selected.map((r) => r.pointer)).toEqual(order);
    expect(other.map((r) => r.pointer)).toEqual(order);
  });

  it("searches across channels", () => {
    const rows = flattenPickerRows(groupsFixture(), {
      query: "v4.1",
      enabledRefs: ALL_BYOK,
    });
    expect(rows.map((r) => r.pointer)).toEqual([
      "@byok/p1/deepseek-v4.1-flash",
    ]);
  });

  it("keeps a hidden current row and drops the rest of the hidden set", () => {
    const rows = flattenPickerRows(groupsFixture(), {
      currentPointer: "@platform/flash-free",
      hiddenRefs: ["@platform/flash-free", "@byok/p2/gpt-4o"],
      enabledRefs: ALL_BYOK,
    });
    expect(rows.map((r) => r.pointer)).toEqual([
      "@byok/p1/deepseek-v4.1-flash",
      "@platform/flash-free",
    ]);
  });

  it("leaves connected-provider rows off the picker until opened", () => {
    const rows = flattenPickerRows(groupsFixture(), {
      currentPointer: "@platform/flash-free",
    });
    expect(rows.map((r) => r.pointer)).toEqual(["@platform/flash-free"]);
  });

  it("sorts persisted hand-filled ids after catalog rows", () => {
    const groups: DefaultProviderGroup[] = [
      {
        providerId: "p1",
        providerLabel: "DeepSeek",
        models: [
          opt("ep-x", "ep-x", { handFilled: true }),
          opt("flash", "Flash"),
        ],
      },
    ];
    const rows = flattenPickerRows(groups, {
      enabledRefs: ["@byok/p1/ep-x", "@byok/p1/flash"],
    });
    expect(rows.map((r) => r.option.model)).toEqual(["flash", "ep-x"]);
  });

  it("folds the active custom id into the list", () => {
    const rows = flattenPickerRows(groupsFixture(), {
      activeCustom: { providerId: "p1", model: "ep-already" },
    });
    expect(rows.some((r) => r.option.model === "ep-already")).toBe(true);
    expect(rows.map((r) => r.pointer)).toEqual([
      "@platform/flash-free",
      "@byok/p1/ep-already",
    ]);
  });
});

describe("byokHandFillGroups", () => {
  it("returns live BYOK channels and skips platform plus orphans", () => {
    const groups: DefaultProviderGroup[] = [
      {
        providerId: PLATFORM_POINTER_ID,
        providerLabel: "平台",
        models: [opt("flash", "Flash", { origin: "platform" })],
      },
      {
        providerId: "p1",
        providerLabel: "DeepSeek",
        models: [opt("flash", "Flash")],
      },
      {
        providerId: "gone",
        providerLabel: "已移除的服务商",
        orphan: true,
        models: [opt("old", "old", { custom: true })],
      },
    ];
    expect(byokHandFillGroups(groups).map((g) => g.providerId)).toEqual(["p1"]);
  });
});

describe("emptyByokGroups", () => {
  it("returns live BYOK channels with no catalog rows", () => {
    const groups: DefaultProviderGroup[] = [
      {
        providerId: "p1",
        providerLabel: "DeepSeek",
        models: [],
      },
      {
        providerId: PLATFORM_POINTER_ID,
        providerLabel: "平台",
        models: [opt("flash", "Flash")],
      },
    ];
    expect(emptyByokGroups(groups).map((g) => g.providerId)).toEqual(["p1"]);
  });
});

describe("settingsChannelGroups", () => {
  it("keeps hidden rows and skips orphans", () => {
    const groups: DefaultProviderGroup[] = [
      ...groupsFixture(),
      {
        providerId: "gone",
        providerLabel: "已移除的服务商",
        orphan: true,
        models: [opt("old", "old", { custom: true })],
      },
    ];
    const sections = settingsChannelGroups(
      groups,
      ["@byok/p2/gpt-4o"],
      ALL_BYOK,
    );
    expect(sections.map((s) => s.group.providerId)).toEqual([
      PLATFORM_POINTER_ID,
      "p1",
      "p2",
    ]);
    const openai = sections.find((s) => s.group.providerId === "p2");
    expect(openai?.rows[0]?.hidden).toBe(true);
    expect(openai?.rows[0]?.pointer).toBe("@byok/p2/gpt-4o");
  });

  it("keeps persisted hand-filled ids on the settings inventory", () => {
    const sections = settingsChannelGroups(
      [
        {
          providerId: "p1",
          providerLabel: "Relay",
          models: [
            opt("claude-sonnet", "Claude Sonnet"),
            opt("deepseek-v4.1-flash", "deepseek-v4.1-flash", {
              handFilled: true,
            }),
          ],
        },
      ],
      [],
      ["@byok/p1/claude-sonnet", "@byok/p1/deepseek-v4.1-flash"],
    );
    expect(sections[0]?.rows.map((r) => r.option.model)).toEqual([
      "claude-sonnet",
      "deepseek-v4.1-flash",
    ]);
    expect(sections[0]?.rows[1]?.hidden).toBe(false);
  });
});

describe("settingsChannelStartsCollapsed", () => {
  it("keeps short channels open and closes aggregator catalogs", () => {
    expect(settingsChannelStartsCollapsed(1)).toBe(false);
    expect(settingsChannelStartsCollapsed(4)).toBe(false);
    expect(settingsChannelStartsCollapsed(5)).toBe(true);
  });
});

describe("clusterSettingsRows", () => {
  it("keeps a single-vendor list untitled", () => {
    const rows = [
      {
        pointer: "@byok/p1/a",
        option: opt("a", "A", { vendor: "DeepSeek" }),
        hidden: true,
      },
      {
        pointer: "@byok/p1/b",
        option: opt("b", "B", { vendor: "DeepSeek" }),
        hidden: true,
      },
    ];
    expect(clusterSettingsRows(rows)).toEqual([{ vendor: null, rows }]);
  });

  it("groups an aggregator catalog by vendor", () => {
    const glm = {
      pointer: "@byok/go/glm-5.2",
      option: opt("glm-5.2", "GLM-5.2", { vendor: "智谱 AI" }),
      hidden: true,
    };
    const grok = {
      pointer: "@byok/go/grok-4.6",
      option: opt("grok-4.6", "grok 4.6", { vendor: "xAI" }),
      hidden: true,
    };
    const clusters = clusterSettingsRows([glm, grok]);
    expect(clusters.map((c) => c.vendor)).toEqual(["智谱 AI", "xAI"]);
    expect(clusters[0]?.rows).toEqual([glm]);
    expect(clusters[1]?.rows).toEqual([grok]);
  });
});

describe("composerShelfLayout", () => {
  it("keeps two panes on a wide chip", () => {
    expect(composerShelfLayout({ narrow: false })).toBe("columns");
  });

  it("stacks when the chip is narrow or inside「＋」", () => {
    expect(composerShelfLayout({ narrow: true })).toBe("stack");
    expect(composerShelfLayout({ plusPanel: true, narrow: false })).toBe(
      "stack",
    );
  });
});
