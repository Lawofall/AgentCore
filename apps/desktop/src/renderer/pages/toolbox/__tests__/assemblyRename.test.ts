import { renameAssemblyRecord } from "@/pages/toolbox/assemblyRename";
import { describe, expect, it, vi } from "vitest";

describe("renameAssemblyRecord", () => {
  it("patches a user assembly in place", async () => {
    const materialize = vi.fn(async (id: string) => id);
    const updateName = vi.fn(async () => undefined);
    const result = await renameAssemblyRecord({
      profileId: "user-1",
      kind: "user",
      currentName: "写稿",
      nextName: " 草稿 ",
      materialize,
      updateName,
    });
    expect(materialize).not.toHaveBeenCalled();
    expect(updateName).toHaveBeenCalledWith("user-1", "草稿");
    expect(result).toEqual({ id: "user-1", changed: true });
  });

  it("materializes a preset before the name is written", async () => {
    const materialize = vi.fn(async () => "owned-1");
    const updateName = vi.fn(async () => undefined);
    const result = await renameAssemblyRecord({
      profileId: "sys",
      kind: "system",
      currentName: "完整",
      nextName: "我的完整",
      materialize,
      updateName,
    });
    expect(materialize).toHaveBeenCalledWith("sys");
    expect(updateName).toHaveBeenCalledWith("owned-1", "我的完整");
    expect(result).toEqual({ id: "owned-1", changed: true });
  });

  it("leaves the record alone when the name did not change", async () => {
    const materialize = vi.fn();
    const updateName = vi.fn();
    const result = await renameAssemblyRecord({
      profileId: "user-1",
      kind: "user",
      currentName: "写稿",
      nextName: " 写稿 ",
      materialize,
      updateName,
    });
    expect(materialize).not.toHaveBeenCalled();
    expect(updateName).not.toHaveBeenCalled();
    expect(result).toEqual({ id: "user-1", changed: false });
  });
});
