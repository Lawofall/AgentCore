import { describe, expect, it } from "vitest";
import {
  CLOUD_BRIDGE_HINT,
  shouldShowCloudBridgeHint,
} from "../cloudBridgeHint";

describe("shouldShowCloudBridgeHint", () => {
  it("shows on the latest assistant when the last turn bridged", () => {
    expect(
      shouldShowCloudBridgeHint({
        via: "cloud_bridge",
        sidecarPreference: "unset",
        isLatestAssistant: true,
      }),
    ).toBe(true);
    expect(CLOUD_BRIDGE_HINT).toBe("本轮经云端协助完成");
    expect(CLOUD_BRIDGE_HINT).not.toContain("本机引擎");
  });

  it("hides while that assistant bubble is still streaming", () => {
    expect(
      shouldShowCloudBridgeHint({
        via: "cloud_bridge",
        sidecarPreference: "unset",
        isLatestAssistant: true,
        isStreaming: true,
      }),
    ).toBe(false);
  });

  it("hides when sidecar is force-off", () => {
    expect(
      shouldShowCloudBridgeHint({
        via: "cloud_bridge",
        sidecarPreference: "off",
        isLatestAssistant: true,
      }),
    ).toBe(false);
  });

  it("hides on older assistant bubbles and when the path is sidecar / unset", () => {
    expect(
      shouldShowCloudBridgeHint({
        via: "cloud_bridge",
        sidecarPreference: "on",
        isLatestAssistant: false,
      }),
    ).toBe(false);
    expect(
      shouldShowCloudBridgeHint({
        via: "sidecar",
        sidecarPreference: "unset",
        isLatestAssistant: true,
      }),
    ).toBe(false);
    expect(
      shouldShowCloudBridgeHint({
        via: null,
        sidecarPreference: "unset",
        isLatestAssistant: true,
      }),
    ).toBe(false);
  });
});
