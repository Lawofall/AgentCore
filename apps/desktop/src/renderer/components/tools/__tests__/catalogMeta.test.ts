import { describe, expect, it } from "vitest";
import { FACE_META, FACE_ORDER } from "../catalogMeta";

describe("catalogMeta faces", () => {
  it("covers every ToolFace in the shared reading order", () => {
    expect(FACE_ORDER).toEqual([
      "file",
      "folder",
      "search",
      "web",
      "execution",
      "host_browser",
      "board",
      "orchestration",
    ]);
    expect(Object.keys(FACE_META)).toEqual(FACE_ORDER);
    expect(FACE_META.folder.label).toBe("文件夹");
    expect(FACE_META.board.label).toBe("白板");
    expect(FACE_META.orchestration.label).toBe("编排");
  });
});
