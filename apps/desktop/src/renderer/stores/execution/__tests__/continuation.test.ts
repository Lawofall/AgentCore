import { describe, expect, it } from "vitest";
import { continuationRootId } from "../continuation";

describe("continuationRootId", () => {
  it("returns the run itself when it has no continuesRunId", () => {
    expect(
      continuationRootId("run-1", [
        { id: "run-1", continuesRunId: null },
        { id: "run-2", continuesRunId: null },
      ]),
    ).toBe("run-1");
  });

  it("walks star continuesRunId back to the root", () => {
    const runs = [
      { id: "run-1", continuesRunId: null },
      { id: "run-1_r2", continuesRunId: "run-1" },
      { id: "run-1_r3", continuesRunId: "run-1" },
    ];
    expect(continuationRootId("run-1_r3", runs)).toBe("run-1");
    expect(continuationRootId("run-1_r2", runs)).toBe("run-1");
    expect(continuationRootId("run-1", runs)).toBe("run-1");
  });

  it("walks a linear continuesRunId chain", () => {
    expect(
      continuationRootId("v3", [
        { id: "v1", continuesRunId: null },
        { id: "v2", continuesRunId: "v1" },
        { id: "v3", continuesRunId: "v2" },
      ]),
    ).toBe("v1");
  });

  it("stops on missing or cyclic links", () => {
    expect(
      continuationRootId("orphan", [{ id: "orphan", continuesRunId: "ghost" }]),
    ).toBe("orphan");
    expect(
      continuationRootId("a", [
        { id: "a", continuesRunId: "b" },
        { id: "b", continuesRunId: "a" },
      ]),
    ).toBe("a");
  });
});
