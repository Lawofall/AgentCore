import { describe, expect, it } from "vitest";
import {
  appendUiOutput,
  formatProcessDuration,
  shouldShowTerminalTab,
  stripAnsi,
} from "../processOutput";

describe("stripAnsi", () => {
  it("removes CSI color sequences", () => {
    expect(stripAnsi("\u001b[31mred\u001b[0m")).toBe("red");
  });

  it("leaves plain text", () => {
    expect(stripAnsi("hello")).toBe("hello");
  });
});

describe("appendUiOutput", () => {
  it("truncates to cap from the head", () => {
    expect(appendUiOutput("abcd", "ef", 5)).toBe("bcdef");
  });
});

describe("shouldShowTerminalTab", () => {
  it("hides when empty", () => {
    expect(shouldShowTerminalTab(0)).toBe(false);
    expect(shouldShowTerminalTab(0, 0)).toBe(false);
    expect(shouldShowTerminalTab(0, 0, 0, false)).toBe(false);
  });

  it("shows when any process exists (alive or historical)", () => {
    expect(shouldShowTerminalTab(1)).toBe(true);
  });

  it("shows when only execution records exist (M2 cloud observation)", () => {
    expect(shouldShowTerminalTab(0, 1)).toBe(true);
  });

  it("shows when user terminals or can open pty (M3)", () => {
    expect(shouldShowTerminalTab(0, 0, 1)).toBe(true);
    expect(shouldShowTerminalTab(0, 0, 0, true)).toBe(true);
  });
});

describe("formatProcessDuration", () => {
  it("formats seconds", () => {
    const start = new Date("2026-01-01T00:00:00.000Z").toISOString();
    const now = Date.parse(start) + 45_000;
    expect(formatProcessDuration(start, now)).toBe("45s");
  });

  it("formats minutes", () => {
    const start = new Date("2026-01-01T00:00:00.000Z").toISOString();
    const now = Date.parse(start) + 125_000;
    expect(formatProcessDuration(start, now)).toBe("2m 5s");
  });

  it("formats hours via the shared duration helper", () => {
    const start = new Date("2026-01-01T00:00:00.000Z").toISOString();
    const now = Date.parse(start) + 3_723_000;
    expect(formatProcessDuration(start, now)).toBe("1h 2m");
  });
});
