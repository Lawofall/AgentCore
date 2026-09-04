import { startNewConversation } from "@/lib/newConversation";
import { isMac } from "@/lib/platform";
import { switchRailConversationByDigit } from "@/lib/railHotkeys";
import { openCurrentConversationTerminal } from "@/services/terminalActions";
import { useSidebarStore } from "@/stores/sidebar";
import { useUIStore } from "@/stores/ui";
import type { NavigateFunction } from "react-router-dom";

function keyLabel(key: string): string {
  return key === "\\" ? "\\" : key.toUpperCase();
}

/** Input types that do not consume letter chords as text (safe to run globals). */
const NON_TEXT_INPUT_TYPES = new Set([
  "button",
  "checkbox",
  "radio",
  "file",
  "submit",
  "reset",
  "range",
  "color",
  "hidden",
  "image",
]);

/**
 * True when the event target is a text-editing surface (input / textarea /
 * contenteditable / select). Global AppShell chords must not steal keys there —
 * except chords marked {@link GlobalShortcut.allowInEditable}.
 */
export function isEditableKeyboardTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.closest !== "function") return false;
  // Prefer the IDL string (jsdom may leave isContentEditable undefined / attr unset).
  const ce =
    typeof el.contentEditable === "string"
      ? el.contentEditable
      : (el.getAttribute?.("contenteditable") ?? "");
  if (
    el.isContentEditable ||
    ce === "true" ||
    ce === "plaintext-only" ||
    el.closest(
      "[contenteditable=true], [contenteditable=''], [contenteditable=plaintext-only]",
    )
  ) {
    return true;
  }
  const tag = el.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = ((el as HTMLInputElement).type || "text").toLowerCase();
    return !NON_TEXT_INPUT_TYPES.has(type);
  }
  return false;
}

/**
 * Whether an AppShell global shortcut should fire for this keydown target.
 * Chords with {@link GlobalShortcut.allowInEditable} always run; others yield
 * to editable focus.
 */
export function shouldRunGlobalShortcut(
  shortcutId: string,
  target: EventTarget | null,
): boolean {
  const shortcut = GLOBAL_SHORTCUTS.find((s) => s.id === shortcutId);
  if (shortcut?.allowInEditable) return true;
  return !isEditableKeyboardTarget(target);
}

/**
 * Canonical key for AppShell matching. Digit chords prefer `e.code` (`Digit1`)
 * so AZERTY / similar layouts still map Ctrl+physical-1 to slot 1; Shift is
 * left to the host (`e.key` of `"!"` must not steal Ctrl+Shift+1).
 */
export function resolveShortcutKey(e: KeyboardEvent): string {
  const digit = /^Digit([1-9])$/.exec(e.code)?.[1];
  if (digit && !e.shiftKey) return digit;
  return e.key.toLowerCase();
}

/** Render a modifier chord the way the host OS shows it (⌘K on macOS, Ctrl+K
 * elsewhere). `key` is the lowercased `e.key` value (e.g. "k", "\\"). Shared by
 * the command palette hints and the shortcuts settings page so the displayed
 * chord never drifts from what the handler actually matches. */
export function chord(key: string): string {
  return isMac ? `⌘${keyLabel(key)}` : `Ctrl+${keyLabel(key)}`;
}

export interface GlobalShortcut {
  id: string;
  /** Human-facing action label (used by the shortcuts settings page). */
  label: string;
  /** Lowercased `e.key` values that fire it (with the platform mod key); the
   * first is canonical, any others are accepted alternates. */
  keys: string[];
  /**
   * `false` = chord matched but this slot is empty; AppShell must not
   * preventDefault (browser tab switch still works). Other handlers return
   * `undefined` (always consume).
   */
  run: (navigate: NavigateFunction, key?: string) => boolean | undefined;
  /** Fire even when focus is in an input / textarea / contenteditable. */
  allowInEditable?: boolean;
  /** Settings page: show first…last as one chord (`Ctrl+1 … Ctrl+9`). */
  compactRange?: boolean;
}

/**
 * Single source of truth for the app's global modifier-chord shortcuts.
 *
 * The AppShell keydown handler dispatches off this table (mod + a matching key),
 * and the 快捷键 settings page renders from it — so adding a global shortcut is a
 * one-line edit here, with no drift between behavior and the documented chord.
 * Plain keys handled elsewhere (e.g. Esc closing the dialog, owned by Radix) are
 * not registered here.
 */
export const GLOBAL_SHORTCUTS: GlobalShortcut[] = [
  {
    id: "command-palette",
    label: "命令面板 / 全局搜索",
    keys: ["k"],
    allowInEditable: true,
    run: () => {
      useUIStore.getState().toggleSearch();
      return undefined;
    },
  },
  {
    id: "switch-rail-conversation",
    label: "切换左侧第 1–9 个对话",
    keys: ["1", "2", "3", "4", "5", "6", "7", "8", "9"],
    allowInEditable: true,
    compactRange: true,
    run: (navigate, key) => switchRailConversationByDigit(key ?? "", navigate),
  },
  {
    id: "new-conversation",
    label: "新建对话",
    keys: ["n"],
    run: (navigate) => {
      startNewConversation(navigate);
      return undefined;
    },
  },
  {
    id: "toggle-sidebar",
    label: "收起 / 展开侧栏",
    keys: ["b", "\\"],
    run: () => {
      useSidebarStore.getState().toggleCollapsed();
      return undefined;
    },
  },
  {
    id: "open-workspace-terminal",
    label: "在终端打开工作区",
    keys: ["`"],
    run: () => {
      void openCurrentConversationTerminal();
      return undefined;
    },
  },
];

/** Display chords for a shortcut (canonical first, then any alternates). */
export function shortcutChords(s: GlobalShortcut): string[] {
  const first = s.keys[0];
  const last = s.keys[s.keys.length - 1];
  if (s.compactRange && first && last && s.keys.length >= 2) {
    return [`${chord(first)} … ${chord(last)}`];
  }
  return s.keys.map(chord);
}
