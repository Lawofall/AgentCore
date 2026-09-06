import { describe, expect, it } from "vitest";
import { grantIpcFields } from "../grantFolderHints";

describe("grantIpcFields", () => {
  it("returns empty when hints are absent", () => {
    expect(grantIpcFields()).toEqual({});
    expect(grantIpcFields({})).toEqual({});
  });

  it("forwards set hint fields", () => {
    expect(
      grantIpcFields({
        path: "C:\\Users\\me\\Desktop\\咨询",
        wellKnown: "desktop",
        targetName: "咨询",
        rootId: "r1",
      }),
    ).toEqual({
      path: "C:\\Users\\me\\Desktop\\咨询",
      wellKnown: "desktop",
      targetName: "咨询",
      rootId: "r1",
    });
  });
});
