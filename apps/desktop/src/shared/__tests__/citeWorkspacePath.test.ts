import { describe, expect, it } from "vitest";
import { posixRel, workspaceRelFromCite } from "../citeWorkspacePath";

describe("workspaceRelFromCite", () => {
  it("returns the container-relative path when dest has no subpath", () => {
    expect(
      workspaceRelFromCite(
        { rootId: "r1" },
        { rootId: "r1", relPath: "docs/guide.md" },
      ),
    ).toBe("docs/guide.md");
  });

  it("strips dest subpath", () => {
    expect(
      workspaceRelFromCite(
        { rootId: "r1", subpath: "work/pkg" },
        { rootId: "r1", relPath: "work/pkg/docs/guide.md" },
      ),
    ).toBe("docs/guide.md");
  });

  it("returns null when the file sits outside dest", () => {
    expect(
      workspaceRelFromCite(
        { rootId: "r1", subpath: "work/pkg" },
        { rootId: "r1", relPath: "other/a.md" },
      ),
    ).toBeNull();
  });

  it("returns null across roots", () => {
    expect(
      workspaceRelFromCite({ rootId: "a" }, { rootId: "b", relPath: "a.md" }),
    ).toBeNull();
  });

  it("posixRel unifies separators", () => {
    expect(posixRel("docs\\guide.md")).toBe("docs/guide.md");
  });
});
