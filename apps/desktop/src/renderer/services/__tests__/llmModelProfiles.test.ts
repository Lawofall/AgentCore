import {
  profileSlotSummary,
  slotDisplayName,
} from "@/services/llmModelProfiles";
import { describe, expect, it } from "vitest";

type ProfileRow = {
  id: string;
  name: string;
  kind: "user";
  is_default: boolean;
  main: { origin: "byok"; model: string; provider_id: string };
  worker: { origin: "byok"; model: string; provider_id: string } | null;
  reasoning_effort?: string | null;
};

const CATALOG = [
  {
    id: "deepseek-v4-pro",
    origin: "byok",
    display_name: "DeepSeek V4 Pro",
    provider_id: "prov-deepseek",
  },
  {
    id: "gpt-4o-mini",
    origin: "byok",
    display_name: "GPT-4o mini",
    provider_id: "prov-openai",
  },
  {
    id: "gpt-4o",
    origin: "byok",
    display_name: "GPT-4o",
    provider_id: "prov-openai",
  },
];

function profile(overrides: Partial<ProfileRow> = {}): ProfileRow {
  return {
    id: "p1",
    name: "日常",
    kind: "user",
    main: {
      origin: "byok",
      model: "deepseek-v4-pro",
      provider_id: "prov-deepseek",
    },
    worker: null,
    is_default: false,
    ...overrides,
  };
}

describe("slotDisplayName", () => {
  it("prefers catalog display_name", () => {
    expect(
      slotDisplayName(
        {
          origin: "byok",
          model: "deepseek-v4-pro",
          provider_id: "prov-deepseek",
        },
        CATALOG,
      ),
    ).toBe("DeepSeek V4 Pro");
  });

  it("uses the catalog display_name for a platform slot", () => {
    expect(
      slotDisplayName(
        { origin: "platform", model: "deepseek-v4-flash", provider_id: null },
        [
          {
            id: "deepseek-v4-flash",
            origin: "platform",
            display_name: "DeepSeek V4 Flash",
          },
        ],
      ),
    ).toBe("DeepSeek V4 Flash");
  });
});

describe("profileSlotSummary", () => {
  it("shows 主 · Worker and 跟随主模型 when worker is empty", () => {
    expect(profileSlotSummary(profile(), CATALOG)).toBe(
      "DeepSeek V4 Pro · 跟随主模型",
    );
  });

  it("shows worker display name when set", () => {
    expect(
      profileSlotSummary(
        profile({
          worker: {
            origin: "byok",
            model: "gpt-4o-mini",
            provider_id: "prov-openai",
          },
        }),
        CATALOG,
      ),
    ).toBe("DeepSeek V4 Pro · GPT-4o mini");
  });

  it("appends vendor effort token when the main model sends reasoning_effort", () => {
    const catalog = [
      {
        ...CATALOG[0],
        reasoning_effort: { options: ["low", "high", "max"], default: "high" },
      },
      ...CATALOG.slice(1),
    ];
    expect(profileSlotSummary(profile(), catalog)).toBe(
      "DeepSeek V4 Pro · 跟随主模型 · high",
    );
    expect(
      profileSlotSummary(profile({ reasoning_effort: "low" }), catalog),
    ).toBe("DeepSeek V4 Pro · 跟随主模型 · low");
  });
});
