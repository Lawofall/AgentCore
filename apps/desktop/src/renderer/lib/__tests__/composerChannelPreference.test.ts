import { hasLocalFiles } from "@/lib/capabilities";
import {
  getComposerChannelPreference,
  setComposerChannelPreference,
  storedComposerChannelPreference,
} from "@/lib/composerChannelPreference";
import {
  __clearMemoryUiStorageForTests,
  __setUiStorageBackendForTests,
  uiStorageKey,
} from "@/lib/uiStorage";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: vi.fn(() => false),
}));

const memory = new Map<string, string>();

describe("composerChannelPreference", () => {
  beforeEach(() => {
    vi.mocked(hasLocalFiles).mockReturnValue(false);
    memory.clear();
    __setUiStorageBackendForTests({
      getItem: (key) => memory.get(key) ?? null,
      setItem: (key, value) => {
        memory.set(key, value);
      },
      removeItem: (key) => {
        memory.delete(key);
      },
      keys: () => [...memory.keys()],
    });
  });

  afterEach(() => {
    __setUiStorageBackendForTests(null);
    __clearMemoryUiStorageForTests();
  });

  it("defaults to cloud when unset and there is no local disk", () => {
    expect(storedComposerChannelPreference()).toBeNull();
    expect(getComposerChannelPreference()).toBe("cloud");
  });

  it("defaults to local_traditional when unset on desktop", () => {
    vi.mocked(hasLocalFiles).mockReturnValue(true);
    expect(storedComposerChannelPreference()).toBeNull();
    expect(getComposerChannelPreference()).toBe("local_traditional");
  });

  it("persists cloud and local_traditional", () => {
    setComposerChannelPreference("local_traditional");
    expect(getComposerChannelPreference()).toBe("local_traditional");

    setComposerChannelPreference("cloud");
    expect(getComposerChannelPreference()).toBe("cloud");
  });

  it("treats corrupt storage as the unset default", () => {
    memory.set(uiStorageKey("composer-channel"), JSON.stringify("nope"));
    expect(getComposerChannelPreference()).toBe("cloud");

    memory.set(uiStorageKey("composer-channel"), "not-json");
    expect(getComposerChannelPreference()).toBe("cloud");
  });
});
