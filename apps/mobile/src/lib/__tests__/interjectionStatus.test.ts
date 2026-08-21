import {
  interjectionStatusLabel,
  interjectionStatusTone,
  showInterjectionStatusChrome,
} from "@/lib/interjectionStatus";
import { describe, expect, it } from "vitest";

describe("interjectionStatusLabel", () => {
  it("maps five states verbatim", () => {
    expect(interjectionStatusLabel("received")).toBe(
      "已送达，等待主 Agent 读取",
    );
    expect(interjectionStatusLabel("injected")).toBe("主 Agent 已看到");
    expect(interjectionStatusLabel("addressed")).toBe("已纳入本回合合成");
    expect(interjectionStatusLabel("queued")).toBe("将在下一条回复处理");
    expect(interjectionStatusLabel("failed")).toBe("未被处理");
  });

  it("hides status chrome only for addressed", () => {
    expect(showInterjectionStatusChrome("addressed")).toBe(false);
    expect(showInterjectionStatusChrome("received")).toBe(true);
    expect(showInterjectionStatusChrome("injected")).toBe(true);
    expect(showInterjectionStatusChrome("queued")).toBe(true);
    expect(showInterjectionStatusChrome("failed")).toBe(true);
  });

  it("turnClosed + received → 未被主 Agent 读取", () => {
    expect(interjectionStatusLabel("received", { turnClosed: true })).toBe(
      "未被主 Agent 读取",
    );
    expect(interjectionStatusLabel("received", { turnClosed: false })).toBe(
      "已送达，等待主 Agent 读取",
    );
  });

  it("turnClosed does not rewrite non-received states", () => {
    expect(interjectionStatusLabel("injected", { turnClosed: true })).toBe(
      "主 Agent 已看到",
    );
    expect(interjectionStatusLabel("failed", { turnClosed: true })).toBe(
      "未被处理",
    );
    expect(interjectionStatusLabel("queued", { turnClosed: true })).toBe(
      "将在下一条回复处理",
    );
    expect(interjectionStatusLabel("addressed", { turnClosed: true })).toBe(
      "已纳入本回合合成",
    );
  });

  it("never says 已传达给团队", () => {
    for (const s of ["received", "injected", "queued", "failed", "addressed"]) {
      expect(interjectionStatusLabel(s)).not.toContain("已传达给团队");
      expect(interjectionStatusLabel(s, { turnClosed: true })).not.toContain(
        "已传达给团队",
      );
    }
  });
});

describe("interjectionStatusTone", () => {
  it("five tones are distinct; addressed is not success", () => {
    expect(interjectionStatusTone("received")).toBe("received");
    expect(interjectionStatusTone("injected")).toBe("injected");
    expect(interjectionStatusTone("addressed")).toBe("addressed");
    expect(interjectionStatusTone("queued")).toBe("queued");
    expect(interjectionStatusTone("failed")).toBe("failed");
    expect(interjectionStatusTone("addressed")).not.toBe("failed");
  });
});
