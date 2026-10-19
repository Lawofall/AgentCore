import {
  PLATFORM_GROUP_LABEL,
  PLATFORM_POINTER_ID,
  buildDefaultProviderGroups,
  catalogItemBadge,
  decodePointer,
  encodePointer,
  modelInChannelCatalog,
  originBadge,
  pointerValue,
  unavailableReasonCopy,
} from "@/lib/llmDefaults";
import type { ModelProfileSlot } from "@/services/llmModelProfiles";
import type { LlmProviderView } from "@/services/llmProviders";
import type { ModelCatalog, ModelCatalogItem } from "@/services/models";
import { catalogItemRef } from "@/services/models";
import { describe, expect, it } from "vitest";

function provider(
  over: Partial<LlmProviderView> & { id: string },
): LlmProviderView {
  return {
    label: "",
    base_url: "https://api.example.com/v1",
    vendor_id: "custom",
    status: "unchecked",
    ...over,
  };
}

function catalogItem(
  over: Partial<ModelCatalogItem> & { id: string; badge?: string | null },
): ModelCatalogItem {
  const origin = over.origin ?? "byok";
  const provider_id = over.provider_id ?? (origin === "platform" ? null : "p1");
  const row = {
    origin,
    display_name: over.id,
    vendor: "V",
    capabilities: [],
    available: true,
    provider_id,
    ...over,
  };
  return {
    ...row,
    custom: over.custom ?? false,
    ref: catalogItemRef(row),
  };
}

function catalog(models: ModelCatalogItem[]): ModelCatalog {
  const first = models[0];
  return {
    byok_configured: true,
    current: first
      ? {
          id: first.id,
          origin: first.origin,
          provider_id: first.provider_id,
          ref: first.ref,
        }
      : { id: "x", origin: "byok", ref: "@byok/p1/x" },
    models,
  };
}

describe("encode/decode pointer", () => {
  it("round-trips a byok slot", () => {
    const slot: ModelProfileSlot = {
      origin: "byok",
      provider_id: "p1",
      model: "m1",
    };
    expect(decodePointer(encodePointer(slot))).toEqual(slot);
  });

  it("round-trips a platform slot", () => {
    const slot: ModelProfileSlot = {
      origin: "platform",
      provider_id: null,
      model: "flash",
    };
    expect(encodePointer(slot)).toBe("@platform/flash");
    expect(decodePointer(encodePointer(slot))).toEqual(slot);
  });

  it("round-trips a byok model id that contains slashes", () => {
    const slot: ModelProfileSlot = {
      origin: "byok",
      provider_id: "p1",
      model: "openai/gpt-4o",
    };
    expect(encodePointer(slot)).toBe("@byok/p1/openai/gpt-4o");
    expect(decodePointer(encodePointer(slot))).toEqual(slot);
  });

  it("still reads leftover __platform__:: pointers", () => {
    expect(decodePointer(`${PLATFORM_POINTER_ID}::flash`)).toEqual({
      origin: "platform",
      provider_id: null,
      model: "flash",
    });
  });

  it("returns null for empty follow value", () => {
    expect(decodePointer("")).toBeNull();
    expect(pointerValue(null)).toBe("");
  });
});

describe("buildDefaultProviderGroups", () => {
  it("lists platform catalog as a live 平台 channel ahead of BYOK providers", () => {
    const groups = buildDefaultProviderGroups(
      [
        provider({
          id: "p1",
          label: "DeepSeek",
        }),
        provider({ id: "p2", label: "OpenAI" }),
      ],
      catalog([
        catalogItem({
          id: "platform-flash",
          origin: "platform",
          display_name: "Flash",
          provider_id: null,
        }),
        catalogItem({
          id: "deepseek-v4-pro",
          provider_id: "p1",
          display_name: "DeepSeek V4 Pro",
        }),
        catalogItem({
          id: "gpt-4o",
          provider_id: "p2",
          display_name: "GPT-4o",
        }),
      ]),
    );
    expect(groups[0]?.providerId).toBe(PLATFORM_POINTER_ID);
    expect(groups[0]?.providerLabel).toBe(PLATFORM_GROUP_LABEL);
    expect(groups[0]?.orphan).toBeFalsy();
    expect(groups[0]?.models.map((m) => m.model)).toEqual(["platform-flash"]);
    expect(groups[0]?.models[0]?.label).toBe("Flash");
    expect(groups[0]?.models[0]?.badge).toBeNull();
    expect(groups[0]?.models[0]?.vendor).toBe("V");
    expect(groups.map((g) => g.providerLabel)).toContain("DeepSeek");
    expect(groups.map((g) => g.providerLabel)).toContain("OpenAI");
    const deepseek = groups.find((g) => g.providerId === "p1");
    expect(deepseek?.models.map((m) => m.model)).not.toContain(
      "platform-flash",
    );
  });

  it("keeps a curated platform SKU badge instead of stamping 平台", () => {
    const groups = buildDefaultProviderGroups(
      [],
      catalog([
        catalogItem({
          id: "flash-free",
          origin: "platform",
          display_name: "Flash",
          provider_id: null,
          badge: "免费额度",
        }),
      ]),
    );
    expect(groups[0]?.models[0]?.label).toBe("Flash");
    expect(groups[0]?.models[0]?.badge).toBe("免费额度");
  });

  it("folds live slot models with catalog display names", () => {
    const groups = buildDefaultProviderGroups(
      [
        provider({
          id: "p1",
          label: "DeepSeek",
        }),
      ],
      catalog([
        catalogItem({
          id: "deepseek-v4-pro",
          provider_id: "p1",
          display_name: "DeepSeek V4 Pro",
        }),
        catalogItem({
          id: "custom-model",
          provider_id: "p1",
          display_name: "Custom Nice Name",
        }),
      ]),
      {
        origin: "byok",
        provider_id: "p1",
        model: "custom-model",
      },
    );
    // already in catalog via default path — label from catalog
    const hit = groups[0].models.find((m) => m.model === "custom-model");
    expect(hit?.label).toBe("Custom Nice Name");
    expect(hit?.custom).toBeFalsy();
  });

  it("marks unknown folded ids as custom with bare-id label", () => {
    const groups = buildDefaultProviderGroups(
      [
        provider({
          id: "p1",
          label: "DeepSeek",
        }),
      ],
      catalog([
        catalogItem({
          id: "deepseek-v4-pro",
          provider_id: "p1",
          display_name: "DeepSeek V4 Pro",
        }),
      ]),
      {
        origin: "byok",
        provider_id: "p1",
        model: "ep-volc-123",
      },
    );
    const hit = groups[0].models.find((m) => m.model === "ep-volc-123");
    expect(hit).toEqual(
      expect.objectContaining({
        model: "ep-volc-123",
        label: "ep-volc-123",
        custom: true,
      }),
    );
  });

  it("does not leak another profile's slot into suggestions", () => {
    const providers = [
      provider({
        id: "p1",
        label: "DeepSeek",
      }),
    ];
    const cat = catalog([
      catalogItem({
        id: "deepseek-v4-flash",
        provider_id: "p1",
        display_name: "DeepSeek V4 Flash",
      }),
    ]);
    // Only fold current profile slots — not the other profile's zen free SKU.
    const currentOnly = buildDefaultProviderGroups(providers, cat, {
      origin: "byok",
      provider_id: "p1",
      model: "deepseek-v4-flash",
    });
    expect(currentOnly[0].models.map((m) => m.model)).toEqual([
      "deepseek-v4-flash",
    ]);
    expect(currentOnly[0].models.map((m) => m.model)).not.toContain(
      "deepseek-v4-flash-free",
    );

    const pollutedIfAllProfiles = buildDefaultProviderGroups(
      providers,
      cat,
      {
        origin: "byok",
        provider_id: "p1",
        model: "deepseek-v4-flash",
      },
      {
        origin: "platform",
        provider_id: null,
        model: "deepseek-v4-flash-free",
      },
    );
    const deepseek = pollutedIfAllProfiles.find((g) => g.providerId === "p1");
    expect(deepseek?.models.map((m) => m.model)).not.toContain(
      "deepseek-v4-flash-free",
    );
  });

  it("folds a live platform slot as an orphan, not a platform catalog group", () => {
    const groups = buildDefaultProviderGroups([], catalog([]), {
      origin: "platform",
      provider_id: null,
      model: "custom-platform",
    });
    expect(groups[0].providerId).toBe(PLATFORM_POINTER_ID);
    expect(groups[0].orphan).toBe(true);
    expect(groups[0].providerLabel).toBe("当前模型");
    expect(groups[0].models.map((m) => m.model)).toEqual(["custom-platform"]);
    expect(groups[0].models[0].custom).toBe(true);
  });

  it("surfaces orphan provider groups for deleted providers", () => {
    const groups = buildDefaultProviderGroups(
      [provider({ id: "p1", label: "DeepSeek" })],
      catalog([]),
      {
        origin: "byok",
        provider_id: "gone-provider",
        model: "orphan-model",
      },
    );
    const orphan = groups.find((g) => g.providerId === "gone-provider");
    expect(orphan?.orphan).toBe(true);
    expect(orphan?.providerLabel).toBe("已移除的服务商");
    expect(orphan?.models[0]).toEqual(
      expect.objectContaining({
        model: "orphan-model",
        custom: true,
      }),
    );
  });

  it("reads optional catalog badge via narrow extension", () => {
    const item = catalogItem({
      id: "free-sku",
      origin: "platform",
      provider_id: null,
      display_name: "Flash",
    });
    expect(catalogItemBadge(item)).toBeNull();
    expect(
      catalogItemBadge({ ...item, badge: "免费额度" } as ModelCatalogItem & {
        badge: string;
      }),
    ).toBe("免费额度");
  });

  it("maps catalog custom rows to handFilled inventory options", () => {
    const groups = buildDefaultProviderGroups(
      [provider({ id: "p1", label: "Relay" })],
      catalog([
        catalogItem({
          id: "deepseek-v4.1-flash",
          provider_id: "p1",
          display_name: "deepseek-v4.1-flash",
          custom: true,
        }),
        catalogItem({
          id: "claude-sonnet",
          provider_id: "p1",
          display_name: "Claude Sonnet",
        }),
      ]),
    );
    const flash = groups[0].models.find(
      (m) => m.model === "deepseek-v4.1-flash",
    );
    const sonnet = groups[0].models.find((m) => m.model === "claude-sonnet");
    expect(flash).toEqual(
      expect.objectContaining({
        custom: false,
        handFilled: true,
      }),
    );
    expect(sonnet?.handFilled).toBeFalsy();
    expect(modelInChannelCatalog(groups[0], "deepseek-v4.1-flash")).toBe(false);
    expect(modelInChannelCatalog(groups[0], "claude-sonnet")).toBe(true);
  });

  it("modelInChannelCatalog ignores custom-only fold-ins", () => {
    const groups = buildDefaultProviderGroups(
      [provider({ id: "p1", label: "DeepSeek" })],
      catalog([
        catalogItem({
          id: "deepseek-v4-flash",
          provider_id: "p1",
          display_name: "Flash",
        }),
      ]),
      { origin: "byok", provider_id: "p1", model: "ep-x" },
    );
    expect(modelInChannelCatalog(groups[0], "deepseek-v4-flash")).toBe(true);
    expect(modelInChannelCatalog(groups[0], "ep-x")).toBe(false);
  });

  it("keeps unavailable catalog rows and carries the structured reason", () => {
    const groups = buildDefaultProviderGroups(
      [
        provider({
          id: "p1",
          label: "OpenCode Go",
        }),
      ],
      catalog([
        catalogItem({
          id: "kimi-k2.5",
          provider_id: "p1",
          display_name: "Kimi K2.5",
        }),
        catalogItem({
          id: "grok-4.5",
          provider_id: "p1",
          display_name: "Grok 4.5",
          available: false,
          unavailable_reason: {
            code: "upstream_protocol_unsupported",
            required_protocol: "openai_responses",
          },
        }),
      ]),
    );
    const go = groups.find((g) => g.providerId === "p1");
    const grok = go?.models.find((m) => m.model === "grok-4.5");
    expect(grok?.available).toBe(false);
    expect(grok?.unavailableReason).toEqual({
      code: "upstream_protocol_unsupported",
      required_protocol: "openai_responses",
    });
    expect(go?.models.find((m) => m.model === "kimi-k2.5")?.available).toBe(
      true,
    );
  });

  it("lists only catalog ids for a provider group", () => {
    const groups = buildDefaultProviderGroups(
      [
        provider({
          id: "p1",
          label: "DeepSeek",
        }),
      ],
      catalog([
        catalogItem({
          id: "deepseek-flash",
          provider_id: "p1",
          display_name: "DeepSeek V4.1 Flash",
        }),
      ]),
    );
    expect(groups[0].models.map((m) => m.model)).toEqual(["deepseek-flash"]);
  });
});

describe("originBadge", () => {
  it("does not stamp 平台 on platform origin", () => {
    expect(originBadge("platform")).toBeNull();
    expect(originBadge("platform", "  ")).toBeNull();
    expect(originBadge("platform", "免费额度")).toBe("免费额度");
  });

  it("leaves BYOK curated badges alone and does not invent a 平台 mark", () => {
    expect(originBadge("byok")).toBeNull();
    expect(originBadge("byok", "免费额度")).toBe("免费额度");
  });
});

describe("unavailableReasonCopy", () => {
  it("renders protocol copy for known codes and stays silent otherwise", () => {
    expect(
      unavailableReasonCopy({
        code: "upstream_protocol_unsupported",
        required_protocol: "openai_responses",
      }),
    ).toBe("需要 OpenAI /responses 协议，当前接入不支持");
    expect(
      unavailableReasonCopy({
        code: "upstream_protocol_unsupported",
        required_protocol: "anthropic_messages",
      }),
    ).toBe("需要 Anthropic /messages 协议，当前接入不支持");
    expect(unavailableReasonCopy(null)).toBeNull();
    expect(unavailableReasonCopy(undefined)).toBeNull();
  });
});
