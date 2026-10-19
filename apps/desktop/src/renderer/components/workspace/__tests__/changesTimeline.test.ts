import {
  localVersionTimelineEntries,
  versionSourceFromWorkspace,
} from "@/components/workspace/changesTimeline";
import { describe, expect, it } from "vitest";

describe("versionSourceFromWorkspace", () => {
  it("routes cloud desks to the snapshot REST id", () => {
    expect(
      versionSourceFromWorkspace({
        location: "cloud",
        wsId: "folder:f1",
        rootId: null,
        subpath: "",
      }),
    ).toEqual({ origin: "cloudWs", wsId: "folder:f1" });
  });

  it("routes bound local desks to sidecar root + subpath", () => {
    expect(
      versionSourceFromWorkspace({
        location: "local",
        wsId: "folder:f2",
        rootId: "root-1",
        subpath: "conversations/c1",
      }),
    ).toEqual({
      origin: "localWs",
      rootId: "root-1",
      subpath: "conversations/c1",
    });
  });

  it("has no source when a local desk is not bound yet", () => {
    expect(
      versionSourceFromWorkspace({
        location: "local",
        wsId: "conv:c1",
        rootId: null,
        subpath: "",
      }),
    ).toBeNull();
  });
});

describe("localVersionTimelineEntries", () => {
  it("maps sidecar versions to the version-card shape", () => {
    expect(
      localVersionTimelineEntries([
        {
          versionId: "v1",
          name: "改前",
          createdAt: "2026-08-01T00:00:00Z",
          sizeBytes: 12,
        },
      ]),
    ).toEqual([
      {
        kind: "version",
        id: "v1",
        title: "改前",
        rawLabel: null,
        sizeBytes: 12,
        at: "2026-08-01T00:00:00Z",
      },
    ]);
  });
});
