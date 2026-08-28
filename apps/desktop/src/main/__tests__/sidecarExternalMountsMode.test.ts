/**
 * Sidecar externalMounts must forward mode so organize is not degraded to readonly.
 * @vitest-environment node
 */
import { describe, expect, it } from "vitest";
import { buildExternalMounts } from "../sidecar/externalMounts";

describe("sidecar externalMounts mode", () => {
  it("preserves organize mode on mapped mounts", () => {
    const mounts = buildExternalMounts([
      {
        id: "r1",
        name: "Desktop",
        absPath: "C:\\Users\\me\\Desktop",
        sessionOnly: true,
        conversationId: "c1",
        mode: "organize",
        alias: "desk",
      },
    ]);
    expect(mounts).toEqual([
      {
        alias: "desk",
        rootId: "r1",
        label: "Desktop",
        absPath: "C:\\Users\\me\\Desktop",
        mode: "organize",
      },
    ]);
  });

  it("preserves attach_rw mode on mapped mounts", () => {
    const mounts = buildExternalMounts([
      {
        id: "r3",
        name: "Trade",
        absPath: "C:\\trade",
        sessionOnly: true,
        conversationId: "c1",
        mode: "attach_rw",
        alias: "trade",
      },
    ]);
    expect(mounts[0]?.mode).toBe("attach_rw");
  });

  it("defaults missing mode to readonly", () => {
    const mounts = buildExternalMounts([
      {
        id: "r2",
        name: "reports",
        absPath: "C:\\reports",
        sessionOnly: true,
        conversationId: "c1",
        alias: "reports",
      },
    ]);
    expect(mounts[0]?.mode).toBe("readonly");
  });
});
