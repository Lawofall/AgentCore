import { describe, expect, it } from "vitest";
import {
  WINDOW_FILL_WARN_RATIO,
  captainCompletedModel,
  catalogContextLength,
  positiveTokens,
  sessionWindowPrompt,
  windowFill,
  windowFillLabel,
} from "../windowFill";

describe("windowFill", () => {
  it("returns null without a real used/window pair", () => {
    expect(windowFill(0, 1_000_000)).toBeNull();
    expect(windowFill(12_000, 0)).toBeNull();
    expect(windowFill(Number.NaN, 1_000_000)).toBeNull();
  });

  it("uses last-prompt / catalog window, not a char heuristic", () => {
    const fill = windowFill(120_000, 1_000_000);
    expect(fill).toEqual({
      used: 120_000,
      window: 1_000_000,
      ratio: 0.12,
      percent: 12,
    });
    expect(windowFillLabel(fill, 120_000)).toBe("120.0k / 1.0M");
    expect(windowFillLabel(null, 120_000)).toBe("120.0k");
    expect(windowFillLabel(null, null)).toBe("收到的上下文");
  });

  it("keeps percent honest above 100 and still warns at the compaction line", () => {
    const over = windowFill(1_100_000, 1_000_000);
    expect(over?.percent).toBe(110);
    expect(over && over.ratio >= WINDOW_FILL_WARN_RATIO).toBe(true);
  });
});

describe("captainCompletedModel", () => {
  it("reads the captain model id from the latest completed frame", () => {
    expect(
      captainCompletedModel([
        { kind: "run_completed", role: "captain", model: "deepseek-v4-flash" },
      ]),
    ).toBe("deepseek-v4-flash");
  });
});

describe("catalogContextLength", () => {
  const models = [
    {
      id: "deepseek-v4-flash",
      origin: "platform" as const,
      display_name: "Flash",
      vendor: "DeepSeek",
      ref: "@platform/deepseek-v4-flash",
      available: true,
      context_length: 1_000_000,
    },
    {
      id: "opencode-zen-free",
      origin: "platform" as const,
      display_name: "Zen",
      vendor: "OpenCode",
      ref: "@platform/opencode-zen-free",
      available: true,
      context_length: 200_000,
    },
  ];

  it("prefers the completed-run model id, then the profile slot", () => {
    expect(
      catalogContextLength(models, {
        modelId: "opencode-zen-free",
        slot: { model: "deepseek-v4-flash", origin: "platform" },
      }),
    ).toBe(200_000);
    expect(
      catalogContextLength(models, {
        slot: { model: "deepseek-v4-flash", origin: "platform" },
      }),
    ).toBe(1_000_000);
  });
});

describe("sessionWindowPrompt", () => {
  it("uses the session waterline even when the open bubble still has an older receipt", () => {
    expect(
      sessionWindowPrompt(40_000, [
        { role: "assistant", usage: { last_prompt: 90_000 } },
      ]),
    ).toBe(40_000);
    expect(positiveTokens(0)).toBeNull();
  });

  it("keeps the previous receipt until this session has measured a request", () => {
    expect(
      sessionWindowPrompt(null, [
        { role: "assistant", usage: { last_prompt: 80_000 } },
        { role: "user" },
        { role: "assistant" },
      ]),
    ).toBe(80_000);
  });

  it("a smaller later measurement replaces the earlier one", () => {
    expect(
      sessionWindowPrompt(30_000, [
        { role: "assistant", usage: { last_prompt: 120_000 } },
      ]),
    ).toBe(30_000);
  });
});
