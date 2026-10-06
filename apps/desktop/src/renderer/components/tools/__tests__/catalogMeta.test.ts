import { describe, expect, it } from "vitest";
import { FACE_META, FACE_ORDER, exceptionAudienceTags } from "../catalogMeta";

describe("catalogMeta faces", () => {
  it("covers every ToolFace in the shared reading order", () => {
    expect(FACE_ORDER).toEqual([
      "file",
      "folder",
      "search",
      "web",
      "execution",
      "host_browser",
      "orchestration",
    ]);
    expect(Object.keys(FACE_META)).toEqual(FACE_ORDER);
    expect(FACE_META.folder.label).toBe("文件夹");
    expect(FACE_META.orchestration.label).toBe("编排");
  });

  it("audience tags skip 全员", () => {
    expect(exceptionAudienceTags(["ceo", "worker"])).toEqual([]);
    expect(exceptionAudienceTags(["ceo"])).toEqual(["CEO"]);
    expect(exceptionAudienceTags(["worker"])).toEqual(["队员"]);
  });
});
