import {
  get,
  isBorrowActive,
  markPromoted,
  set,
} from "@/lib/borrowOriginalPreference";
import {
  __clearMemoryUiStorageForTests,
  __setUiStorageBackendForTests,
} from "@/lib/uiStorage";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const memory = new Map<string, string>();

describe("borrowOriginalPreference", () => {
  beforeEach(() => {
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

  it("stores by folderId and isBorrowActive until promoted", () => {
    set("f1", { rootId: "root-a", originalName: "MyApp", promoted: false });
    expect(get("f1")).toEqual({
      rootId: "root-a",
      originalName: "MyApp",
      promoted: false,
    });
    expect(isBorrowActive("f1")).toBe(true);
    expect(isBorrowActive("missing")).toBe(false);

    markPromoted("f1");
    expect(get("f1")).toEqual({
      rootId: "root-a",
      originalName: "MyApp",
      promoted: true,
    });
    expect(isBorrowActive("f1")).toBe(false);
  });

  it("isolates folders and ignores empty writes", () => {
    set("f1", { rootId: "root-a", originalName: "A", promoted: false });
    set("  ", { rootId: "root-b", originalName: "B", promoted: false });
    set("f2", { rootId: "  ", originalName: "B", promoted: false });

    expect(get("f1")).toEqual({
      rootId: "root-a",
      originalName: "A",
      promoted: false,
    });
    expect(get("f2")).toBeNull();
    expect(get("")).toBeNull();

    markPromoted("missing");
    expect(get("f1")?.promoted).toBe(false);
  });
});
