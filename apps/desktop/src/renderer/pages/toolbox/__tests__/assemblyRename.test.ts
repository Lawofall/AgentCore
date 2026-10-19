import { renameAssemblyRecord } from "@/pages/toolbox/assemblyRename";
import { describe, expect, it, vi } from "vitest";

describe("renameAssemblyRecord", () => {
  it("patches a user assembly in place", async () => {
    const updateName = vi.fn(async () => undefined);
    const result = await renameAssemblyRecord({
      profileId: "user-1",
      kind: "user",
      currentName: "写稿",
      nextName: " 草稿 ",
      updateName,
    });
    expect(updateName).toHaveBeenCalledWith("user-1", "草稿");
    expect(result).toEqual({ id: "user-1", changed: true });
  });

  it("does not rename an official recipe", async () => {
    const updateName = vi.fn(async () => undefined);
    const result = await renameAssemblyRecord({
      profileId: "sys",
      kind: "system",
      currentName: "完整",
      nextName: "我的完整",
      updateName,
    });
    expect(updateName).not.toHaveBeenCalled();
    expect(result).toEqual({ id: "sys", changed: false });
  });

  it("leaves the record alone when the name did not change", async () => {
    const updateName = vi.fn();
    const result = await renameAssemblyRecord({
      profileId: "user-1",
      kind: "user",
      currentName: "写稿",
      nextName: " 写稿 ",
      updateName,
    });
    expect(updateName).not.toHaveBeenCalled();
    expect(result).toEqual({ id: "user-1", changed: false });
  });
});
