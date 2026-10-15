import { describe, expect, it } from "vitest";
import {
  desktopDownloadUrlForChannel,
  otherReleaseChannel,
  parseReleaseChannel,
  resolveChannelFromArgv,
  resolveReleaseIdentity,
} from "../../scripts/release-channel.mjs";

describe("release-channel.mjs", () => {
  it("defaults empty / unset to stable", () => {
    expect(parseReleaseChannel(undefined)).toBe("stable");
    expect(parseReleaseChannel("")).toBe("stable");
    expect(parseReleaseChannel("  STABLE ")).toBe("stable");
  });

  it("accepts beta and rejects unknown", () => {
    expect(parseReleaseChannel("beta")).toBe("beta");
    expect(() => parseReleaseChannel("canary")).toThrow(/Invalid/);
  });

  it("resolves stable identity (appId / productName / publish url)", () => {
    const id = resolveReleaseIdentity("stable");
    expect(id).toMatchObject({
      channel: "stable",
      appId: "xyz.fashitianxia.gagent",
      productName: "GAgent",
      shortcutName: "GAgent",
      publishUrl: "https://downloads.fashitianxia.xyz/desktop/stable",
      artifactSlug: "GAgent",
      channelLabelZh: "稳定",
    });
    expect(id.windowsAppUserModelId).toBe(id.appId);
  });

  it("resolves beta identity (appId / productName / publish url)", () => {
    const id = resolveReleaseIdentity("beta");
    expect(id).toMatchObject({
      channel: "beta",
      appId: "xyz.fashitianxia.gagent.beta",
      productName: "GAgent 测试版",
      shortcutName: "GAgent 测试版",
      publishUrl: "https://downloads.fashitianxia.xyz/desktop/beta",
      artifactSlug: "GAgent",
      channelLabelZh: "测试",
    });
    expect(id.windowsAppUserModelId).toBe(id.appId);
  });

  it("parses --channel from argv over env", () => {
    expect(
      resolveChannelFromArgv(["--channel=beta"], {
        DESKTOP_RELEASE_CHANNEL: "stable",
      }),
    ).toBe("beta");
    expect(
      resolveChannelFromArgv(["--channel", "beta"], {}),
    ).toBe("beta");
    expect(
      resolveChannelFromArgv([], { DESKTOP_RELEASE_CHANNEL: "beta" }),
    ).toBe("beta");
    expect(resolveChannelFromArgv([], {})).toBe("stable");
  });

  it("maps other channel and website download query", () => {
    expect(otherReleaseChannel("stable")).toBe("beta");
    expect(desktopDownloadUrlForChannel("beta")).toBe(
      "https://fashitianxia.xyz/download?channel=beta",
    );
  });
});
