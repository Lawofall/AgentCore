import {
  TOOLS_GATE_HINT,
  TOOL_CALLING_TOOL_NAMES,
  needsToolsGateHint,
} from "@/lib/llmToolsGate";
import { describe, expect, it } from "vitest";

describe("llmToolsGate", () => {
  it("flags only explicit false as needing a soft hint", () => {
    expect(needsToolsGateHint(false)).toBe(true);
    expect(needsToolsGateHint(true)).toBe(false);
    expect(needsToolsGateHint(null)).toBe(false);
    expect(needsToolsGateHint(undefined)).toBe(false);
  });

  it("covers delegate/debate tool names for soft-hint surfaces", () => {
    expect(TOOL_CALLING_TOOL_NAMES.has("delegate")).toBe(true);
    expect(TOOL_CALLING_TOOL_NAMES.has("debate")).toBe(true);
    expect(TOOL_CALLING_TOOL_NAMES.has("debate_and_review")).toBe(false);
    expect(TOOL_CALLING_TOOL_NAMES.has("team_orchestration_advanced")).toBe(
      false,
    );
  });

  it("uses non-absolute soft copy (no hard「不支持」assertion)", () => {
    expect(TOOLS_GATE_HINT).toMatch(/未确认|可能降级|以运行为准/);
    expect(TOOLS_GATE_HINT).not.toMatch(/不支持工具调用/);
  });
});
