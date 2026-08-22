import {
  BYOK_PROVIDER_PRESETS,
  DEFAULT_BYOK_PROVIDER_ID,
  getByokProviderPreset,
  listByokProviderOptions,
  normalizeByokBaseUrl,
  resolveByokProviderFromConfig,
} from "@/lib/byokProviderPresets";
import { describe, expect, it } from "vitest";

describe("normalizeByokBaseUrl", () => {
  it("lowercases and strips trailing slashes", () => {
    expect(normalizeByokBaseUrl("HTTPS://API.OpenAI.com/v1/")).toBe(
      "https://api.openai.com/v1",
    );
  });
});

describe("resolveByokProviderFromConfig", () => {
  it("defaults empty base_url to DeepSeek preset", () => {
    expect(resolveByokProviderFromConfig("")).toBe(DEFAULT_BYOK_PROVIDER_ID);
    expect(DEFAULT_BYOK_PROVIDER_ID).toBe("deepseek");
  });

  it("matches canonical preset base_url", () => {
    expect(resolveByokProviderFromConfig("https://api.openai.com/v1")).toBe(
      "openai",
    );
    expect(
      resolveByokProviderFromConfig("https://open.bigmodel.cn/api/paas/v4"),
    ).toBe("zhipu");
  });

  it("matches aliases (DeepSeek /v1, Moonshot international)", () => {
    expect(resolveByokProviderFromConfig("https://api.deepseek.com/v1")).toBe(
      "deepseek",
    );
    expect(resolveByokProviderFromConfig("https://api.moonshot.ai/v1")).toBe(
      "moonshot",
    );
  });

  it("matches OpenCode Zen base_url", () => {
    expect(resolveByokProviderFromConfig("https://opencode.ai/zen/v1")).toBe(
      "opencode_zen",
    );
    expect(resolveByokProviderFromConfig("https://opencode.ai/zen/v1/")).toBe(
      "opencode_zen",
    );
    expect(resolveByokProviderFromConfig("HTTPS://OPENCODE.AI/ZEN/V1/")).toBe(
      "opencode_zen",
    );
  });

  it("matches OpenCode Go base_url without confusing it with Zen", () => {
    expect(resolveByokProviderFromConfig("https://opencode.ai/zen/go/v1")).toBe(
      "opencode_go",
    );
    expect(
      resolveByokProviderFromConfig("https://opencode.ai/zen/go/v1/"),
    ).toBe("opencode_go");
    expect(
      resolveByokProviderFromConfig("HTTPS://OpenCode.AI/ZEN/GO/V1/"),
    ).toBe("opencode_go");
    expect(resolveByokProviderFromConfig("https://opencode.ai/zen/v1")).toBe(
      "opencode_zen",
    );
    expect(
      resolveByokProviderFromConfig("https://opencode.ai/zen/go/v1"),
    ).not.toBe("opencode_zen");
    expect(
      resolveByokProviderFromConfig("https://opencode.ai/zen/v1"),
    ).not.toBe("opencode_go");
    // Prefix-style leftovers must not steal either preset.
    expect(resolveByokProviderFromConfig("https://opencode.ai/zen")).toBe(
      "custom",
    );
    expect(resolveByokProviderFromConfig("https://opencode.ai/zen/v1/go")).toBe(
      "custom",
    );
    expect(resolveByokProviderFromConfig("https://opencode.ai/zen/go")).toBe(
      "custom",
    );
  });

  it("matches Hy TokenHub domestic, backup, and intl base_urls", () => {
    expect(
      resolveByokProviderFromConfig("https://tokenhub.tencentmaas.com/v1"),
    ).toBe("hy");
    expect(
      resolveByokProviderFromConfig("https://tokenhub.tencentmaas.cn/v1"),
    ).toBe("hy");
    expect(
      resolveByokProviderFromConfig("https://tokenhub-intl.tencentmaas.com/v1"),
    ).toBe("hy");
    expect(
      resolveByokProviderFromConfig("https://tokenhub-intl.tencentmaas.cn/v1/"),
    ).toBe("hy");
  });

  it("falls back to custom for unknown endpoints", () => {
    // Stored base_url that matches no preset stays custom — no remap or wipe.
    expect(resolveByokProviderFromConfig("https://my-proxy.example/v1")).toBe(
      "custom",
    );
  });

  it("treats trailing slash variants as the same preset", () => {
    expect(resolveByokProviderFromConfig("https://api.deepseek.com/")).toBe(
      "deepseek",
    );
  });
});

describe("getByokProviderPreset", () => {
  it("returns DeepSeek flash-first metadata", () => {
    const preset = getByokProviderPreset("deepseek");
    expect(preset.baseUrl).toBe("https://api.deepseek.com");
    expect(preset.defaultModel).toBe("deepseek-v4-flash");
    expect(preset.models).toEqual(["deepseek-v4-flash", "deepseek-v4-pro"]);
    expect(preset.models).not.toContain("deepseek-chat");
  });

  it("lists DeepSeek first among vendor presets", () => {
    expect(BYOK_PROVIDER_PRESETS[0]?.id).toBe("deepseek");
    expect(BYOK_PROVIDER_PRESETS[1]?.id).toBe("openai");
  });

  it("defaults Moonshot to kimi-k2.6 with current models", () => {
    const preset = getByokProviderPreset("moonshot");
    expect(preset.defaultModel).toBe("kimi-k2.6");
    expect(preset.models).toEqual(["kimi-k2.6", "kimi-k3", "kimi-k2.5"]);
    expect(preset.models).not.toContain("kimi-k2");
    expect(preset.models).not.toContain("moonshot-v1-8k");
  });

  it("defaults Doubao to seed turbo and drops retired pro/lite ids", () => {
    const preset = getByokProviderPreset("doubao");
    expect(preset.defaultModel).toBe("doubao-seed-2-1-turbo-260628");
    expect(preset.models).toEqual(["doubao-seed-2-1-turbo-260628"]);
    expect(preset.models).not.toContain("doubao-pro-32k");
    expect(preset.models).not.toContain("doubao-lite-32k");
  });

  it("returns Hy TokenHub domestic metadata", () => {
    const preset = getByokProviderPreset("hy");
    expect(preset.label).toBe("腾讯 Hy (TokenHub)");
    expect(preset.baseUrl).toBe("https://tokenhub.tencentmaas.com/v1");
    expect(preset.baseUrlAliases).toEqual([
      "https://tokenhub.tencentmaas.cn/v1",
      "https://tokenhub-intl.tencentmaas.com/v1",
      "https://tokenhub-intl.tencentmaas.cn/v1",
    ]);
    expect(preset.defaultModel).toBe("hy3");
    expect(preset.models).toEqual(["hy3", "hy3-preview"]);
    expect(preset.keyHelpUrl).toBe(
      "https://console.cloud.tencent.com/tokenhub/apikey",
    );
  });

  it("returns OpenCode Zen metadata with short discovery seed", () => {
    const preset = getByokProviderPreset("opencode_zen");
    expect(preset.id).toBe("opencode_zen");
    expect(preset.label).toBe("OpenCode Zen");
    expect(preset.baseUrl).toBe("https://opencode.ai/zen/v1");
    expect(preset.defaultModel).toBe("deepseek-v4-flash");
    expect(preset.models).toEqual([
      "deepseek-v4-flash",
      "kimi-k2.6",
      "glm-5.2",
    ]);
    expect(preset.keyHelpUrl).toBe("https://opencode.ai/auth");
  });

  it("returns OpenCode Go metadata with chat/completions seed", () => {
    const preset = getByokProviderPreset("opencode_go");
    expect(preset.id).toBe("opencode_go");
    expect(preset.label).toBe("OpenCode Go");
    expect(preset.baseUrl).toBe("https://opencode.ai/zen/go/v1");
    expect(preset.defaultModel).toBe("deepseek-v4-flash");
    expect(preset.models).toEqual([
      "deepseek-v4-flash",
      "deepseek-v4-pro",
      "glm-5.2",
    ]);
    expect(preset.keyHelpUrl).toBe("https://opencode.ai/auth");
    expect(preset.models.join(" ")).not.toMatch(/-free|grok|gpt|minimax|qwen/i);
    const labels = listByokProviderOptions().map((opt) => opt.label);
    expect(labels).toContain("OpenCode Zen");
    expect(labels).toContain("OpenCode Go");
  });

  it("keeps preset base_urls unique after normalize (Zen vs Go)", () => {
    const urls = BYOK_PROVIDER_PRESETS.flatMap((preset) =>
      [preset.baseUrl, ...(preset.baseUrlAliases ?? [])].map(
        normalizeByokBaseUrl,
      ),
    );
    expect(new Set(urls).size).toBe(urls.length);
  });
});
