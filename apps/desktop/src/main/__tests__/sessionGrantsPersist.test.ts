/**
 * W3 conversation-scoped session grants persistence (fs-session-grants.json).
 * @vitest-environment node
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
  app: { getPath: () => "/tmp/agentcore-test-userdata" },
}));

import type { StoredRoot } from "../fs/roots";
import { __test as rootsTest } from "../fs/roots";

describe("fs-session-grants payload", () => {
  beforeEach(() => {
    rootsTest.reset();
  });

  it("groups sessionOnly roots by conversationId and skips permanent roots", () => {
    rootsTest.reset(
      new Map<string, StoredRoot>([
        ["perm", { id: "perm", name: "proj", absPath: "C:\\proj" }],
        [
          "s1",
          {
            id: "s1",
            name: "reports",
            absPath: "C:\\data\\reports",
            sessionOnly: true,
            conversationId: "c1",
            mode: "readonly",
            alias: "reports",
          },
        ],
        [
          "s2",
          {
            id: "s2",
            name: "desk",
            absPath: "C:\\Users\\desk",
            sessionOnly: true,
            conversationId: "c1",
            mode: "organize",
            alias: "desk",
            workingRoot: true,
          },
        ],
        [
          "s3",
          {
            id: "s3",
            name: "other",
            absPath: "C:\\other",
            sessionOnly: true,
            conversationId: "c2",
            mode: "readonly",
            alias: "other",
          },
        ],
      ]),
    );

    const payload = rootsTest.buildSessionFilePayload();
    expect(Object.keys(payload).sort()).toEqual(["c1", "c2"]);
    expect(payload.c1.map((r) => r.id).sort()).toEqual(["s1", "s2"]);
    expect(payload.c1.find((r) => r.id === "s2")?.mode).toBe("organize");
    expect(payload.c1.find((r) => r.id === "s2")?.workingRoot).toBe(true);
    expect(payload.c1.find((r) => r.id === "s1")?.workingRoot).toBe(false);
    expect(payload.c2).toHaveLength(1);
    expect(payload).not.toHaveProperty("perm");
  });

  it("rehydrates session grants from file payload into the roots map", () => {
    rootsTest.applySessionFilePayload({
      c9: [
        {
          id: "ext-9",
          name: "inbox",
          absPath: "D:\\inbox",
          sessionOnly: true,
          conversationId: "c9",
          mode: "organize",
          alias: "inbox",
        },
      ],
    });
    const map = rootsTest.getMap();
    const row = map.get("ext-9");
    expect(row?.absPath).toBe("D:\\inbox");
    expect(row?.conversationId).toBe("c9");
    expect(row?.mode).toBe("organize");
    expect(row?.sessionOnly).toBe(true);
  });

  it("migrates legacy readonly-boolean grants to mode-only on load", () => {
    rootsTest.applySessionFilePayload({
      "c-legacy": [
        {
          id: "legacy-ro",
          name: "old",
          absPath: "C:\\old",
          sessionOnly: true,
          conversationId: "c-legacy",
          readonly: true,
          alias: "old",
        },
        {
          id: "legacy-org",
          name: "desk",
          absPath: "C:\\desk",
          sessionOnly: true,
          conversationId: "c-legacy",
          readonly: false,
          alias: "desk",
        },
      ],
    });
    const map = rootsTest.getMap();
    expect(map.get("legacy-ro")?.mode).toBe("readonly");
    expect(map.get("legacy-org")?.mode).toBe("organize");
    expect(map.get("legacy-ro")).not.toHaveProperty("readonly");
    expect(map.get("legacy-org")).not.toHaveProperty("readonly");

    const payload = rootsTest.buildSessionFilePayload();
    for (const row of payload["c-legacy"] ?? []) {
      expect(row).not.toHaveProperty("readonly");
      expect(row.mode === "readonly" || row.mode === "organize").toBe(true);
    }
  });
});
