import { afterEach, describe, expect, it } from "vitest";
import {
  clearActiveSidecarTurn,
  getActiveSidecarTarget,
  resetSidecarRoutingForTests,
  resolveSidecarControlTarget,
  resolveSidecarControlTargetForEngine,
  setActiveSidecarTurn,
} from "../sidecarRouting";

const CID = "conv-control";

afterEach(() => {
  resetSidecarRoutingForTests();
});

describe("resolveSidecarControlTarget", () => {
  it("keeps last turnId after the live map is cleared (stream teardown ≠ engine gone)", () => {
    setActiveSidecarTurn(CID, "root-1", "conversations/x", "turn-live");
    clearActiveSidecarTurn(CID);

    expect(getActiveSidecarTarget(CID)).toBeNull();
    expect(resolveSidecarControlTarget(CID)).toEqual({
      rootId: "root-1",
      subpath: "conversations/x",
      turnId: "turn-live",
    });
  });

  it("prefers the live map while it is still registered", () => {
    setActiveSidecarTurn(CID, "root-1", "", "turn-a");
    expect(resolveSidecarControlTarget(CID)?.turnId).toBe("turn-a");
  });
});

describe("resolveSidecarControlTargetForEngine", () => {
  it("does not fall back to the conversation local root for cloud_bridge", async () => {
    await expect(
      resolveSidecarControlTargetForEngine(CID, "cloud_bridge"),
    ).resolves.toBeNull();
  });

  it("returns last after live clear without needing executionVia", async () => {
    setActiveSidecarTurn(CID, "root-1", "sub", "turn-live");
    clearActiveSidecarTurn(CID);
    await expect(
      resolveSidecarControlTargetForEngine(CID, null),
    ).resolves.toEqual({
      rootId: "root-1",
      subpath: "sub",
      turnId: "turn-live",
    });
  });
});
