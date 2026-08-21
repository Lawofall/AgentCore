import { describe, expect, it } from "vitest";
import {
  INTERJECTION_TONE_CLASS,
  interjectionStatusLabel,
  interjectionStatusTone,
  isInterjectionTurnTerminal,
  showInterjectionStatusChrome,
} from "../interjectionStatus";

describe("interjectionStatusLabel", () => {
  it("maps five states verbatim", () => {
    expect(interjectionStatusLabel("received")).toBe(
      "已送达，等待主 Agent 读取",
    );
    expect(interjectionStatusLabel("injected")).toBe("主 Agent 已看到");
    expect(interjectionStatusLabel("queued")).toBe("将在下一条回复处理");
    expect(interjectionStatusLabel("failed")).toBe("未被处理");
    expect(interjectionStatusLabel("addressed")).toBe("已纳入本回合合成");
  });

  it("hides status chrome only for addressed", () => {
    expect(showInterjectionStatusChrome("addressed")).toBe(false);
    expect(showInterjectionStatusChrome("received")).toBe(true);
    expect(showInterjectionStatusChrome("injected")).toBe(true);
    expect(showInterjectionStatusChrome("queued")).toBe(true);
    expect(showInterjectionStatusChrome("failed")).toBe(true);
  });

  it("renders unread copy when turn is terminal and status stays received", () => {
    expect(interjectionStatusLabel("received", { turnTerminal: true })).toBe(
      "未被主 Agent 读取",
    );
    expect(interjectionStatusLabel("received", { turnTerminal: false })).toBe(
      "已送达，等待主 Agent 读取",
    );
  });

  it("never says 已传达给团队", () => {
    for (const s of [
      "received",
      "injected",
      "queued",
      "failed",
      "addressed",
      "unknown",
    ]) {
      expect(interjectionStatusLabel(s)).not.toContain("已传达给团队");
      expect(interjectionStatusLabel(s, { turnTerminal: true })).not.toContain(
        "已传达给团队",
      );
    }
  });
});

describe("isInterjectionTurnTerminal", () => {
  it("is true for terminal / idle; false only while live bubble streams", () => {
    expect(isInterjectionTurnTerminal("completed", false)).toBe(true);
    expect(isInterjectionTurnTerminal("stopped", true)).toBe(true);
    expect(isInterjectionTurnTerminal("failed", undefined)).toBe(true);
    expect(isInterjectionTurnTerminal("idle", false)).toBe(true);
    expect(isInterjectionTurnTerminal("streaming", true)).toBe(false);
    expect(isInterjectionTurnTerminal("stopping", true)).toBe(false);
    expect(isInterjectionTurnTerminal("streaming", false)).toBe(true);
  });
});

describe("interjectionStatusTone", () => {
  it("addressed is not success-green tone", () => {
    expect(interjectionStatusTone("addressed")).toBe("addressed");
    expect(INTERJECTION_TONE_CLASS.addressed).not.toContain("success");
    expect(interjectionStatusTone("received")).toBe("received");
    expect(interjectionStatusTone("injected")).toBe("injected");
    expect(interjectionStatusTone("queued")).toBe("queued");
    expect(interjectionStatusTone("failed")).toBe("failed");
  });

  it("five tones are visually distinct classes", () => {
    const classes = [
      INTERJECTION_TONE_CLASS.received,
      INTERJECTION_TONE_CLASS.injected,
      INTERJECTION_TONE_CLASS.addressed,
      INTERJECTION_TONE_CLASS.queued,
      INTERJECTION_TONE_CLASS.failed,
    ];
    expect(new Set(classes).size).toBe(5);
  });
});
