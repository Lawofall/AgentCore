import { diffRowClass, diffSign, diffSignClass } from "@/lib/diffChrome";
import { describe, expect, it } from "vitest";

describe("diffChrome", () => {
  it("uses dedicated add/del washes and tinted signs", () => {
    expect(diffSign("add")).toBe("+");
    expect(diffSign("del")).toBe("-");
    expect(diffSign("context")).toBe(" ");
    expect(diffRowClass("add")).toBe("bg-diff-add text-foreground");
    expect(diffRowClass("del")).toBe("bg-diff-del text-foreground");
    expect(diffRowClass("context")).toBe("text-muted-foreground");
    expect(diffSignClass("add")).toBe("bg-diff-add-gutter text-diff-add-fg");
    expect(diffSignClass("del")).toBe("bg-diff-del-gutter text-diff-del-fg");
    expect(diffSignClass("context")).toBe("text-muted-foreground/50");
  });
});
