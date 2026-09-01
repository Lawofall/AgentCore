import { shouldMountInlineGraphHost } from "@/components/graph/graphHost";
import { describe, expect, it } from "vitest";

describe("shouldMountInlineGraphHost", () => {
  it("mounts only when expanded and on-screen", () => {
    expect(shouldMountInlineGraphHost({ expanded: true, inView: true })).toBe(
      true,
    );
    expect(shouldMountInlineGraphHost({ expanded: true, inView: false })).toBe(
      false,
    );
    expect(shouldMountInlineGraphHost({ expanded: false, inView: true })).toBe(
      false,
    );
    expect(shouldMountInlineGraphHost({ expanded: false, inView: false })).toBe(
      false,
    );
  });
});
