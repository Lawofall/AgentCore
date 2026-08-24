import { describe, expect, it } from "vitest";
import { isPreviewScreenshot } from "../fileArtifacts";

describe("isPreviewScreenshot", () => {
  it("true when kind=image and derivedFrom is set", () => {
    expect(
      isPreviewScreenshot({
        path: "p.png",
        name: "p.png",
        kind: "image",
        derivedFrom: "site/index.html",
      }),
    ).toBe(true);
  });

  it("false without derivedFrom or wrong kind", () => {
    expect(
      isPreviewScreenshot({ path: "p.png", name: "p.png", kind: "image" }),
    ).toBe(false);
    expect(
      isPreviewScreenshot({
        path: "x.html",
        name: "x.html",
        kind: "html",
        derivedFrom: "y.html",
      }),
    ).toBe(false);
  });
});
