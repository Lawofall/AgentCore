/**
 * Local console ring-buffer: caps, scrub, level normalize (no electron).
 */

import { describe, expect, it } from "vitest";
import {
  CONSOLE_MAX_MESSAGES,
  ConsoleRingBuffer,
  isElectronHostSecurityWarning,
  normalizeConsoleLevel,
  scrubConsoleText,
} from "../browser/console-buffer";

describe("scrubConsoleText", () => {
  it("redacts password= values and truncates blobs", () => {
    expect(scrubConsoleText("password=hunter2 ok")).toContain("=[redacted]");
    const blob = "A".repeat(5000);
    const out = scrubConsoleText(blob);
    expect(out.endsWith("…[truncated blob]")).toBe(true);
    expect(out.length).toBeLessThan(120);
  });

  it("truncates long non-blob text", () => {
    const text = "hello world! ".repeat(50); // has spaces → not blob
    const out = scrubConsoleText(text);
    expect(out.endsWith("…")).toBe(true);
    expect(out.length).toBeLessThanOrEqual(500);
  });
});

describe("normalizeConsoleLevel", () => {
  it("maps legacy ints and strings", () => {
    expect(normalizeConsoleLevel(3)).toBe("error");
    expect(normalizeConsoleLevel("Warning")).toBe("warning");
    expect(normalizeConsoleLevel(null)).toBe("info");
  });
});

describe("isElectronHostSecurityWarning", () => {
  it("drops Electron's own CSP warning and keeps page violations", () => {
    expect(
      isElectronHostSecurityWarning(
        "Electron Security Warning (Insecure Content-Security-Policy) This renderer process has either no Content Security Policy",
      ),
    ).toBe(true);
    const buf = new ConsoleRingBuffer();
    buf.pushMessage(
      "warning",
      "Electron Security Warning (Insecure Content-Security-Policy)",
    );
    buf.pushMessage(
      "error",
      "Refused to execute inline script because it violates the following Content Security Policy directive",
    );
    const snap = buf.snapshot();
    expect(snap.messages).toHaveLength(1);
    expect(snap.messages[0]?.text).toContain("Refused to execute");
  });
});

describe("ConsoleRingBuffer", () => {
  it("drops oldest messages and reports truncated counts", () => {
    const buf = new ConsoleRingBuffer();
    for (let i = 0; i < CONSOLE_MAX_MESSAGES + 5; i++) {
      buf.pushMessage("log", `line-${i}`, i);
    }
    const snap = buf.snapshot();
    expect(snap.messages).toHaveLength(CONSOLE_MAX_MESSAGES);
    expect(snap.truncated.messages_dropped).toBe(5);
    expect(snap.messages[0]?.text).toBe("line-5");
    expect(snap.messages.at(-1)?.text).toBe(`line-${CONSOLE_MAX_MESSAGES + 4}`);
  });

  it("stores errors with truncated stack", () => {
    const buf = new ConsoleRingBuffer();
    buf.pushError("boom", "stack\n".repeat(400), 1.5);
    const snap = buf.snapshot();
    expect(snap.errors).toHaveLength(1);
    expect(snap.errors[0]?.message).toBe("boom");
    expect(snap.errors[0]?.stack?.length).toBeLessThanOrEqual(1500);
    expect(snap.errors[0]?.timestamp).toBe(1.5);
  });
});
