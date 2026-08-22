import { compareSemver, isAndroidVersionOutdated } from "@/lib/androidSemver";
import { describe, expect, it } from "vitest";

describe("androidSemver", () => {
  it("orders major.minor.patch", () => {
    expect(compareSemver("0.4.9", "0.4.10")).toBeLessThan(0);
    expect(compareSemver("0.5.0", "0.4.10")).toBeGreaterThan(0);
    expect(compareSemver("1.0.0", "1.0.0")).toBe(0);
  });

  it("does not flag dev or empty remote", () => {
    expect(isAndroidVersionOutdated("dev", "1.0.0")).toBe(false);
    expect(isAndroidVersionOutdated("0.4.0", null)).toBe(false);
    expect(isAndroidVersionOutdated("0.4.0", "0.4.1")).toBe(true);
  });
});
