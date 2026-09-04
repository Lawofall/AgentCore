// @vitest-environment jsdom
import {
  isRailHotkeyHintModifier,
  subscribeRailHotkeyHint,
} from "@/lib/railHotkeys";
import { afterEach, describe, expect, it } from "vitest";

describe("isRailHotkeyHintModifier", () => {
  it("arms on Ctrl only (Win/Linux)", () => {
    expect(
      isRailHotkeyHintModifier(
        { altKey: false, shiftKey: false, ctrlKey: true, metaKey: false },
        false,
      ),
    ).toBe(true);
  });

  it("arms on Cmd only (Mac)", () => {
    expect(
      isRailHotkeyHintModifier(
        { altKey: false, shiftKey: false, ctrlKey: false, metaKey: true },
        true,
      ),
    ).toBe(true);
  });

  it("ignores Shift / Alt / the other platform mod", () => {
    const winCtrl = {
      altKey: false,
      shiftKey: false,
      ctrlKey: true,
      metaKey: false,
    };
    expect(
      isRailHotkeyHintModifier({ ...winCtrl, shiftKey: true }, false),
    ).toBe(false);
    expect(isRailHotkeyHintModifier({ ...winCtrl, altKey: true }, false)).toBe(
      false,
    );
    expect(
      isRailHotkeyHintModifier(
        { altKey: false, shiftKey: false, ctrlKey: true, metaKey: false },
        true,
      ),
    ).toBe(false);
    expect(
      isRailHotkeyHintModifier(
        { altKey: false, shiftKey: false, ctrlKey: false, metaKey: true },
        false,
      ),
    ).toBe(false);
  });

  it("matches AppShell rail switch: Mac Ctrl+digit does not arm", () => {
    expect(
      isRailHotkeyHintModifier(
        { altKey: false, shiftKey: false, ctrlKey: true, metaKey: false },
        true,
      ),
    ).toBe(false);
  });
});

describe("subscribeRailHotkeyHint", () => {
  const seen: boolean[] = [];
  afterEach(() => {
    seen.length = 0;
  });

  it("reveals on Control down and hides on up / blur", () => {
    const stop = subscribeRailHotkeyHint((v) => seen.push(v));
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Control", ctrlKey: true }),
    );
    window.dispatchEvent(
      new KeyboardEvent("keyup", { key: "Control", ctrlKey: false }),
    );
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Control", ctrlKey: true }),
    );
    window.dispatchEvent(new Event("blur"));
    stop();
    expect(seen).toEqual([true, false, true, false]);
  });
});
