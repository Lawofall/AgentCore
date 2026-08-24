import { describe, expect, it } from "vitest";
import { resolveWorkspaceImageRef } from "../isWorkspaceImageRef";
import { parseCompareFence } from "../parseCompareFence";

describe("resolveWorkspaceImageRef", () => {
  it("accepts workspace-relative image paths", () => {
    expect(resolveWorkspaceImageRef("site/v1.png")).toBe("site/v1.png");
    expect(resolveWorkspaceImageRef("/workspace/site/v2.jpg")).toBe(
      "site/v2.jpg",
    );
  });

  it("rejects external and scheme URLs (PI-001)", () => {
    expect(resolveWorkspaceImageRef("https://evil.example/x.png")).toBeNull();
    expect(resolveWorkspaceImageRef("//cdn.example/x.png")).toBeNull();
    expect(resolveWorkspaceImageRef("data:image/png;base64,abc")).toBeNull();
  });

  it("rejects non-image extensions", () => {
    expect(resolveWorkspaceImageRef("site/index.html")).toBeNull();
  });

  it("rejects leftover absolute and drive paths", () => {
    expect(resolveWorkspaceImageRef("/etc/foo.png")).toBeNull();
    expect(resolveWorkspaceImageRef("C:/secret.png")).toBeNull();
    expect(resolveWorkspaceImageRef("../secret.png")).toBeNull();
  });
});

describe("parseCompareFence", () => {
  const twoPane = `A|方案一
site/v1.png
---
B|方案二
site/v2.png`;

  it("parses slot|label + path panes separated by ---", () => {
    const panes = parseCompareFence(twoPane);
    expect(panes).toEqual([
      { slot: "A", label: "方案一", path: "site/v1.png" },
      { slot: "B", label: "方案二", path: "site/v2.png" },
    ]);
  });

  it("parses markdown image syntax", () => {
    const panes = parseCompareFence("![左](a.png)\n---\n![右](b.png)");
    expect(panes).toEqual([
      { label: "左", path: "a.png" },
      { label: "右", path: "b.png" },
    ]);
  });

  it("returns null when fewer than two valid panes", () => {
    expect(parseCompareFence("A|only\nsite/a.png")).toBeNull();
    expect(parseCompareFence("")).toBeNull();
  });

  it("returns null when any pane references an external URL", () => {
    expect(
      parseCompareFence(`A|一
ok.png
---
B|二
https://evil/x.png`),
    ).toBeNull();
  });
});
