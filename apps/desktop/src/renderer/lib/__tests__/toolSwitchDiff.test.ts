import { describe, expect, it } from "vitest";
import { conversationToolDiffLabel } from "../toolSwitchDiff";

describe("conversationToolDiffLabel", () => {
  it("相同则不写", () => {
    expect(conversationToolDiffLabel([], [])).toBeNull();
    expect(
      conversationToolDiffLabel(["web", "files"], ["files", "web"]),
    ).toBeNull();
  });

  it("这场多关的只数这场关了", () => {
    expect(conversationToolDiffLabel(["files", "run"], [])).toBe(
      "这场关了 2 样",
    );
    expect(conversationToolDiffLabel(["files", "web"], ["web"])).toBe(
      "这场关了 1 样",
    );
  });

  it("这场把账户关掉的又打开，写这场开了", () => {
    expect(conversationToolDiffLabel([], ["ask_user"])).toBe("这场开了 1 样");
  });

  it("两边都有差，写差了几样", () => {
    expect(conversationToolDiffLabel(["files"], ["web"])).toBe(
      "这场和默认差 2 样",
    );
  });
});
