import {
  type FolderMeta,
  dedupeFoldersByLocalBinding,
  findLocalFolderByBinding,
  folderHasCollaborators,
} from "@/services/folders";
import { describe, expect, it } from "vitest";

const local = (
  id: string,
  root: string,
  sub: string | null = null,
): FolderMeta => ({
  id,
  name: id,
  mode: "local",
  localRootId: root,
  localSubpath: sub,
});

const cloud = (id: string): FolderMeta => ({
  id,
  name: id,
  mode: "cloud",
  localRootId: null,
  localSubpath: null,
});

describe("local folder binding helpers", () => {
  it("findLocalFolderByBinding matches null and empty subpath", () => {
    const folders = [local("a", "root-1", null), local("b", "root-1", "apps")];
    expect(findLocalFolderByBinding(folders, "root-1", null)?.id).toBe("a");
    expect(findLocalFolderByBinding(folders, "root-1", "")?.id).toBe("a");
    expect(findLocalFolderByBinding(folders, "root-1", "apps")?.id).toBe("b");
    expect(findLocalFolderByBinding(folders, "missing", null)).toBeUndefined();
  });

  it("dedupeFoldersByLocalBinding keeps first local binding and all clouds", () => {
    const folders = [
      local("oldest", "root-1", null),
      cloud("c1"),
      local("dup", "root-1", ""),
      local("other", "root-1", "apps"),
      cloud("c2"),
    ];
    expect(dedupeFoldersByLocalBinding(folders).map((f) => f.id)).toEqual([
      "oldest",
      "c1",
      "other",
      "c2",
    ]);
  });
});

describe("folderHasCollaborators", () => {
  it("is true only for the owner's own cloud desk with a roster", () => {
    expect(
      folderHasCollaborators({ ...cloud("a"), collaboratorCount: 2 }),
    ).toBe(true);
    expect(
      folderHasCollaborators({ ...cloud("a"), collaboratorCount: 0 }),
    ).toBe(false);
    expect(folderHasCollaborators(cloud("a"))).toBe(false);
    expect(
      folderHasCollaborators({
        ...cloud("a"),
        myRole: "editor",
        collaboratorCount: 2,
      }),
    ).toBe(false);
    expect(
      folderHasCollaborators({
        ...local("a", "root-1"),
        collaboratorCount: 2,
      }),
    ).toBe(false);
  });
});
