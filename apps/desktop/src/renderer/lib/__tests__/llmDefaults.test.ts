import {
  PLATFORM_POINTER_ID,
  buildDefaultProviderGroups,
  catalogItemBadge,
  decodePointer,
  encodePointer,
  modelInChannelCatalog,
  pointerValue,
  unavailableReasonCopy,
} from "@/lib/llmDefaults";
import type { ModelProfileSlot } from "@/services/llmModelProfiles";
import type { LlmProviderView } from "@/services/llmProviders";
import type { ModelCatalog, ModelCatalogItem } from "@/services/models";
import { describe, expect, it } from "vitest";

function provider(
  over: Partial<LlmProviderView> & { id: string },
): LlmProviderView {
  return {
    label: "",
    base_url: "https://api.example.com/v1",
    default_model: "model-default",
    status: "unchecked",
    ...over,
  };
}

function catalogItem(
  over: Partial<ModelCatalogItem> & { id: string },
): ModelCatalogItem {
  return {
    origin: "byok",
    display_name: over.id,
    vendor: "V",
    capabilities: [],
    available: true,
    provider_id: over.provider_id ?? "p1",
    ...over,
  };
}

function catalog(models: ModelCatalogItem[]): ModelCatalog {
  return {
    byok_configured: true,
    current: { id: models[0]?.id ?? "x", origin: "byok" },
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
    expect(encodePointer(slot)).toBe(`${PLATFORM_POINTER_ID}::flash`);
    expect(decodePointer(encodePointer(slot))).toEqual(slot);
  });

  it("returns null for empty follow value", () => {
    expect(decodePointer("")).toBeNull();
    expect(pointerValue(null)).toBe("");
  });
});

describe("buildDefaultProviderGroups", () => {
  it("groups byok models under their provider and pins platform first", () => {
    const groups = buildDefaultProviderGroups(
      [
        provider({
          id: "p1",
          label: "DeepSeek",
          default_model: "deepseek-v4-pro",
        }),
        provider({ id: "p2", label: "OpenAI", default_model: "gpt-4o" }),
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
    expect(groups[0].providerLabel).toBe("平台额度");
    expect(groups[0].models.map((m) => m.model)).toEqual(["platform-flash"]);
    expect(groups.map((g) => g.providerLabel)).toContain("DeepSeek");
    expect(groups.map((g) => g.providerLabel)).toContain("OpenAI");
  });

  it("folds live slot models with catalog display names", () => {
    const groups = buildDefaultProviderGroups(
      [
        provider({
          id: "p1",
          label: "DeepSeek",
          default_model: "deepseek-v4-pro",
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
          default_model: "deepseek-v4-pro",
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
        default_model: "deepseek-v4-flash",
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
    // If caller wrongly passes other profiles, platform free SKU lands in platform group — not DeepSeek.
    const deepseek = pollutedIfAllProfiles.find((g) => g.providerId === "p1");
    expect(deepseek?.models.map((m) => m.model)).not.toContain(
      "deepseek-v4-flash-free",
    );
  });

  it("creates a platform group when only a live platform slot exists", () => {
    const groups = buildDefaultProviderGroups([], catalog([]), {
      origin: "platform",
      provider_id: null,
      model: "custom-platform",
    });
    expect(groups[0].providerId).toBe(PLATFORM_POINTER_ID);
    expect(groups[0].models.map((m) => m.model)).toEqual(["custom-platform"]);
    expect(groups[0].models[0].custom).toBe(true);
  });

  it("surfaces orphan provider groups for deleted providers", () => {
    const groups = buildDefaultProviderGroups(
      [provider({ id: "p1", label: "DeepSeek", default_model: "" })],
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

  it("modelInChannelCatalog ignores custom-only fold-ins", () => {
    const groups = buildDefaultProviderGroups(
      [provider({ id: "p1", label: "DeepSeek", default_model: "" })],
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
          default_model: "kimi-k2.5",
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
