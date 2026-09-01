// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Unified UI persistence: namespace, JSON round-trip, preview → memory backend,
 * conversation-scoped keys + clearConversationUiState.
 */

const PREFIX = "agentcore:";

async function fresh() {
  vi.resetModules();
  const mod = await import("@/lib/uiStorage");
  return mod;
}

beforeEach(() => {
  localStorage.clear();
  // Ensure preview flag is off unless a test opts in.
  window.__WEB_PREVIEW__ = undefined;
});

afterEach(async () => {
  localStorage.clear();
  window.__WEB_PREVIEW__ = undefined;
  const mod = await import("@/lib/uiStorage");
  mod.__setUiStorageBackendForTests(null);
  mod.__clearMemoryUiStorageForTests();
});

describe("uiStorage", () => {
  it("namespaces keys under agentcore: and JSON-round-trips values", async () => {
    const { uiGet, uiSet, uiStorageKey } = await fresh();
    expect(uiStorageKey("theme")).toBe(`${PREFIX}theme`);
    expect(uiStorageKey(`${PREFIX}theme`)).toBe(`${PREFIX}theme`);

    uiSet("theme", "dark");
    expect(localStorage.getItem(`${PREFIX}theme`)).toBe(JSON.stringify("dark"));
    expect(uiGet<string>("theme")).toBe("dark");

    uiSet("sidebar-collapsed", true);
    expect(uiGet<boolean>("sidebar-collapsed")).toBe(true);
  });

  it("removes a key when set to undefined", async () => {
    const { uiGet, uiSet, uiRemove } = await fresh();
    uiSet("theme", "light");
    uiSet("theme", undefined);
    expect(localStorage.getItem(`${PREFIX}theme`)).toBeNull();
    expect(uiGet("theme")).toBeUndefined();

    uiSet("theme", "system");
    uiRemove("theme");
    expect(uiGet("theme")).toBeUndefined();
  });

  it("uses an in-memory backend under #/preview (isWebPreview)", async () => {
    window.__WEB_PREVIEW__ = true;
    const { uiGet, uiSet, __clearMemoryUiStorageForTests } = await fresh();
    __clearMemoryUiStorageForTests();

    uiSet("theme", "dark");
    expect(localStorage.getItem(`${PREFIX}theme`)).toBeNull();
    expect(uiGet<string>("theme")).toBe("dark");
  });

  it("conversation-scoped keys + clearConversationUiState wipe them", async () => {
    const {
      conversationUiGet,
      conversationUiSet,
      conversationStorageKey,
      clearConversationUiState,
      uiSet,
      uiGet,
    } = await fresh();

    conversationUiSet("c1", "note", { text: "keep me scoped" });
    conversationUiSet("c2", "note", { text: "other" });
    uiSet("theme", "dark");

    expect(conversationStorageKey("c1", "note")).toBe(`${PREFIX}c:c1:note`);
    expect(conversationUiGet<{ text: string }>("c1", "note")?.text).toBe(
      "keep me scoped",
    );

    clearConversationUiState("c1");

    expect(conversationUiGet("c1", "note")).toBeUndefined();
    expect(conversationUiGet<{ text: string }>("c2", "note")?.text).toBe(
      "other",
    );
    expect(uiGet<string>("theme")).toBe("dark");
  });

  it("runs registered conversation clearers", async () => {
    const { registerConversationUiClearer, clearConversationUiState } =
      await fresh();
    const seen: string[] = [];
    registerConversationUiClearer((id) => {
      seen.push(id);
    });
    clearConversationUiState("conv-x");
    expect(seen).toEqual(["conv-x"]);
  });

  it("createZustandUiStorage normalizes persist names", async () => {
    const { createZustandUiStorage } = await fresh();
    const storage = createZustandUiStorage();
    storage.setItem("sidebar", JSON.stringify({ state: { collapsed: true } }));
    expect(localStorage.getItem(`${PREFIX}sidebar`)).toContain("collapsed");
    expect(storage.getItem("sidebar")).toContain("collapsed");
    storage.removeItem("sidebar");
    expect(localStorage.getItem(`${PREFIX}sidebar`)).toBeNull();
  });
});
