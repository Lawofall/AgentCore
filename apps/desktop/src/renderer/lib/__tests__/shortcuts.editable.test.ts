// @vitest-environment jsdom
import {
  GLOBAL_SHORTCUTS,
  isEditableKeyboardTarget,
  resolveShortcutKey,
  shortcutChords,
  shouldRunGlobalShortcut,
} from "@/lib/shortcuts";
import { describe, expect, it } from "vitest";

describe("isEditableKeyboardTarget", () => {
  it("detects text input / textarea / contenteditable", () => {
    const input = document.createElement("input");
    input.type = "text";
    expect(isEditableKeyboardTarget(input)).toBe(true);

    const area = document.createElement("textarea");
    expect(isEditableKeyboardTarget(area)).toBe(true);

    const editable = document.createElement("div");
    editable.contentEditable = "true";
    expect(isEditableKeyboardTarget(editable)).toBe(true);
  });

  it("ignores non-text inputs and plain elements", () => {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    expect(isEditableKeyboardTarget(checkbox)).toBe(false);

    const button = document.createElement("button");
    expect(isEditableKeyboardTarget(button)).toBe(false);

    expect(isEditableKeyboardTarget(null)).toBe(false);
  });
});

describe("shouldRunGlobalShortcut", () => {
  it("always allows command palette even in an editable", () => {
    const input = document.createElement("input");
    expect(shouldRunGlobalShortcut("command-palette", input)).toBe(true);
  });

  it("allows rail 1–9 switch even in an editable", () => {
    const input = document.createElement("input");
    expect(shouldRunGlobalShortcut("switch-rail-conversation", input)).toBe(
      true,
    );
  });

  it("blocks navigation / sidebar chords while editing", () => {
    const input = document.createElement("input");
    expect(shouldRunGlobalShortcut("new-conversation", input)).toBe(false);
    expect(shouldRunGlobalShortcut("toggle-sidebar", input)).toBe(false);
    expect(shouldRunGlobalShortcut("open-workspace-terminal", input)).toBe(
      false,
    );
  });

  it("allows non-palette chords outside editables", () => {
    const div = document.createElement("div");
    expect(shouldRunGlobalShortcut("new-conversation", div)).toBe(true);
  });
});

describe("resolveShortcutKey", () => {
  it("maps DigitN without Shift to the slot digit", () => {
    expect(
      resolveShortcutKey(
        new KeyboardEvent("keydown", {
          key: "&",
          code: "Digit1",
          shiftKey: false,
        }),
      ),
    ).toBe("1");
  });

  it("leaves Shift+Digit to the host", () => {
    expect(
      resolveShortcutKey(
        new KeyboardEvent("keydown", {
          key: "!",
          code: "Digit1",
          shiftKey: true,
        }),
      ),
    ).toBe("!");
  });
});

describe("shortcutChords", () => {
  it("compacts the rail 1–9 range to one label", () => {
    const rail = GLOBAL_SHORTCUTS.find(
      (s) => s.id === "switch-rail-conversation",
    );
    expect(rail).toBeDefined();
    if (!rail) return;
    const chords = shortcutChords(rail);
    expect(chords).toHaveLength(1);
    expect(chords[0]).toMatch(/1 … .*9/);
  });
});
