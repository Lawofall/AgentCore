import {
  type ModelCatalogItem,
  catalogReasoningEffort,
  resolvedProfileEffort,
} from "@/services/models";
import { describe, expect, it } from "vitest";

const flash: ModelCatalogItem = {
  id: "deepseek-v4-flash",
  ref: "@byok/p1/deepseek-v4-flash",
  origin: "byok",
  display_name: "DeepSeek V4 Flash",
  vendor: "DeepSeek",
  provider_id: "p1",
  capabilities: [],
  available: true,
  custom: false,
  reasoning_effort: { options: ["low", "high", "max"], default: "high" },
};

const gpt: ModelCatalogItem = {
  id: "gpt-4o",
  ref: "@byok/p2/gpt-4o",
  origin: "byok",
  display_name: "GPT-4o",
  vendor: "OpenAI",
  provider_id: "p2",
  capabilities: [],
  available: true,
  custom: false,
};

describe("catalogReasoningEffort", () => {
  it("returns official tokens for a catalog row that sends reasoning_effort", () => {
    const spec = catalogReasoningEffort(
      { model: "deepseek-v4-flash", origin: "byok", provider_id: "p1" },
      [flash, gpt],
    );
    expect(spec?.options).toEqual(["low", "high", "max"]);
    expect(spec?.default).toBe("high");
    expect(spec?.options).not.toContain("medium");
  });

  it("returns null when the leaf does not send the field", () => {
    expect(
      catalogReasoningEffort(
        { model: "gpt-4o", origin: "byok", provider_id: "p2" },
        [flash, gpt],
      ),
    ).toBeNull();
    expect(catalogReasoningEffort(null, [flash])).toBeNull();
  });
});

describe("resolvedProfileEffort", () => {
  it("uses stored token when it is in the catalog options", () => {
    expect(
      resolvedProfileEffort(
        {
          main: {
            model: "deepseek-v4-flash",
            origin: "byok",
            provider_id: "p1",
          },
          reasoning_effort: "low",
        },
        [flash, gpt],
      ),
    ).toBe("low");
  });

  it("falls back to the catalog default when stored is empty", () => {
    expect(
      resolvedProfileEffort(
        {
          main: {
            model: "deepseek-v4-flash",
            origin: "byok",
            provider_id: "p1",
          },
          reasoning_effort: null,
        },
        [flash, gpt],
      ),
    ).toBe("high");
  });

  it("returns null when the leaf does not send the field", () => {
    expect(
      resolvedProfileEffort(
        {
          main: { model: "gpt-4o", origin: "byok", provider_id: "p2" },
          reasoning_effort: "low",
        },
        [flash, gpt],
      ),
    ).toBeNull();
  });
});
