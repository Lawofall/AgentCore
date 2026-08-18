// @vitest-environment jsdom
/**
 * Unit tests for model catalog helpers + model-profile display / last-used memory.
 */
import {
  clearLastModelProfileId,
  getLastModelProfileId,
  profileDisplayLabel,
  profileSlotsSummary,
  resolveDisplayProfile,
  setLastModelProfileId,
  slotDisplayName,
} from "@/api/modelProfiles";
import type { ModelCatalog } from "@/api/models";
import { modelDisplayLabel, unavailableReasonCopy } from "@/api/models";
import { afterEach, describe, expect, it } from "vitest";

const CATALOG: ModelCatalog = {
  byok_configured: true,
  current: {
    id: "deepseek-v4-pro",
    origin: "byok",
    provider_id: "prov-deepseek",
  },
  models: [
    {
      id: "deepseek-v4-pro",
      origin: "byok",
      provider_id: "prov-deepseek",
      provider_label: "DeepSeek",
      display_name: "DeepSeek V4 Pro",
      vendor: "DeepSeek",
      capabilities: [],
      context_length: null,
      price: null,
      available: true,
    },
    {
      id: "deepseek-v4-pro",
      origin: "platform",
      provider_id: null,
      provider_label: null,
      display_name: "DeepSeek V4 Pro (平台)",
      vendor: "DeepSeek",
      capabilities: [],
      context_length: null,
      price: null,
      available: true,
    },
    {
      id: "gpt-4o",
      origin: "byok",
      provider_id: "prov-openai",
      provider_label: "OpenAI",
      display_name: "GPT-4o",
      vendor: "OpenAI",
      capabilities: [],
      context_length: null,
      price: null,
      available: true,
    },
  ],
};

afterEach(() => {
  clearLastModelProfileId();
});

describe("modelDisplayLabel (slot catalog)", () => {
  it("maps a pick (id, origin) to its catalog display name", () => {
    expect(
      modelDisplayLabel(CATALOG, { id: "deepseek-v4-pro", origin: "byok" }),
    ).toBe("DeepSeek V4 Pro");
    expect(
      modelDisplayLabel(CATALOG, {
        id: "deepseek-v4-pro",
        origin: "platform",
      }),
    ).toBe("DeepSeek V4 Pro (平台)");
  });

  it("returns the raw id for an id not in the catalog", () => {
    expect(
      modelDisplayLabel(CATALOG, { id: "mystery-model", origin: "byok" }),
    ).toBe("mystery-model");
  });

  it("returns null when nothing is known yet", () => {
    expect(modelDisplayLabel(null, null)).toBeNull();
  });
});

describe("unavailableReasonCopy", () => {
  it("renders protocol copy for known codes", () => {
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
  });
});

describe("profileSlotsSummary", () => {
  it("shows 主 · Worker and 跟随主模型 when worker is empty", () => {
    expect(
      profileSlotsSummary(CATALOG, {
        id: "p1",
        name: "GLM-5.2",
        kind: "system",
        main: {
          origin: "byok",
          model: "deepseek-v4-pro",
          provider_id: "prov-deepseek",
        },
        worker: null,
        background: null,
        is_default: true,
      }),
    ).toBe("DeepSeek V4 Pro · 跟随主模型");

    expect(
      profileSlotsSummary(CATALOG, {
        id: "p2",
        name: "写作",
        kind: "user",
        main: {
          origin: "byok",
          model: "deepseek-v4-pro",
          provider_id: "prov-deepseek",
        },
        worker: {
          origin: "byok",
          model: "gpt-4o",
          provider_id: "prov-openai",
        },
        background: null,
        is_default: false,
      }),
    ).toBe("DeepSeek V4 Pro · GPT-4o");
  });

  it("appends 后台 / 识图 only when configured (list rows stay compact)", () => {
    expect(
      profileSlotsSummary(CATALOG, {
        id: "p3",
        name: "分槽",
        kind: "user",
        main: {
          origin: "byok",
          model: "deepseek-v4-pro",
          provider_id: "prov-deepseek",
        },
        worker: null,
        background: {
          origin: "byok",
          model: "gpt-4o",
          provider_id: "prov-openai",
        },
        vision: {
          origin: "byok",
          model: "gpt-4o",
          provider_id: "prov-openai",
        },
        is_default: false,
      }),
    ).toBe("DeepSeek V4 Pro · 跟随主模型 · 后台 GPT-4o · 识图 GPT-4o");
  });

  it("falls back to raw model id via slotDisplayName", () => {
    expect(
      slotDisplayName(CATALOG, {
        origin: "platform",
        model: "unknown-x",
        provider_id: null,
      }),
    ).toBe("unknown-x");
  });
});

describe("profileDisplayLabel", () => {
  const list = {
    default_model_profile_id: "def",
    data: [
      {
        id: "def",
        name: "GLM-5.2",
        kind: "system" as const,
        main: {
          origin: "platform" as const,
          model: "deepseek-v4-pro",
          provider_id: null,
        },
        worker: null,
        background: null,
        is_default: true,
      },
      {
        id: "u1",
        name: "写作强档",
        kind: "user" as const,
        main: {
          origin: "byok" as const,
          model: "gpt-4o",
          provider_id: "prov-openai",
        },
        worker: null,
        background: null,
        is_default: false,
      },
    ],
  };

  it("prefers the conversation override name", () => {
    expect(profileDisplayLabel(list, "u1")).toBe("写作强档");
  });

  it("falls back to the account default name", () => {
    expect(profileDisplayLabel(list, null)).toBe("GLM-5.2");
  });

  it("resolves the whole profile so callers can read kind", () => {
    expect(resolveDisplayProfile(list, "u1")?.kind).toBe("user");
    expect(resolveDisplayProfile(list, null)?.kind).toBe("system");
    expect(resolveDisplayProfile(null, null)).toBeNull();
  });

  it("falls back to the account default when the snapshot id is unknown", () => {
    expect(resolveDisplayProfile(list, "gone")?.id).toBe("def");
  });
});

describe("last-used model profile", () => {
  it("round-trips a profile id and clears back to null", () => {
    expect(getLastModelProfileId()).toBeNull();
    setLastModelProfileId("prof-1");
    expect(getLastModelProfileId()).toBe("prof-1");
    clearLastModelProfileId();
    expect(getLastModelProfileId()).toBeNull();
  });
});
