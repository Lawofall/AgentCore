import { describe, expect, it } from "vitest";
import { filesFocusState } from "../constants";

describe("filesFocusState", () => {
  it("focuses a folder workspace", () => {
    expect(filesFocusState("f1")).toEqual({
      state: { focusWsId: "folder:f1" },
    });
  });

  it("returns undefined without a folder — hub does not list conv: scratch", () => {
    expect(filesFocusState(null)).toBeUndefined();
    expect(filesFocusState(undefined)).toBeUndefined();
    expect(filesFocusState("")).toBeUndefined();
  });
});
