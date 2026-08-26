import { describe, expect, it } from "vitest";
import { folderGroupInsertPlace } from "../folderGroupDragFeedback";

describe("folderGroupInsertPlace", () => {
  it("is null when not dragging", () => {
    expect(folderGroupInsertPlace("f2", null, "f2", "before")).toBeNull();
  });

  it("is null on the source row", () => {
    expect(folderGroupInsertPlace("f1", "f1", "f1", "after")).toBeNull();
  });

  it("returns the hover place on another row", () => {
    expect(folderGroupInsertPlace("f2", "f1", "f2", "after")).toBe("after");
    expect(folderGroupInsertPlace("f2", "f1", "f2", "before")).toBe("before");
  });
});
