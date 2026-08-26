// @vitest-environment jsdom

import { queryClient } from "@/lib/queryClient";
import { notifyError } from "@/lib/toast";
import {
  type DocumentDetail,
  type DocumentNode,
  createRuleDocument,
  listScopeEntries,
} from "@/services/documents";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/documents", () => ({
  listScopeEntries: vi.fn(),
  createRuleDocument: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

import { createAndOpenScopeEntry, nextEntryName } from "../createScopeEntry";

const doc = (over: Partial<DocumentNode> = {}): DocumentNode => ({
  id: "e",
  parentId: null,
  folderId: null,
  kind: "document",
  role: "rule",
  aiMaintained: false,
  applyMode: "always",
  description: "",
  name: "e.md",
  frontmatterError: null,
  disputedAt: null,
  alwaysChars: 0,
  ...over,
});

const detail = (over: Partial<DocumentDetail> = {}): DocumentDetail => ({
  ...doc(over),
  content: "",
  version: "v",
  quotaWarning: null,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  queryClient.clear();
});

afterEach(() => {
  queryClient.clear();
});

describe("nextEntryName", () => {
  it("uses 新条目.md then 新条目 2.md", () => {
    expect(nextEntryName([])).toBe("新条目.md");
    expect(nextEntryName(["新条目.md"])).toBe("新条目 2.md");
    expect(nextEntryName(["新条目.md", "新条目 2.md"])).toBe("新条目 3.md");
  });
});

describe("createAndOpenScopeEntry", () => {
  it("creates, invalidates the list, and opens the new entry", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([doc({ name: "语气.md" })]);
    vi.mocked(createRuleDocument).mockResolvedValue(
      detail({ id: "new", name: "新条目.md" }),
    );
    const onOpen = vi.fn();
    expect(await createAndOpenScopeEntry({ kind: "global" }, onOpen)).toBe(
      true,
    );
    expect(listScopeEntries).toHaveBeenCalledWith(null);
    expect(createRuleDocument).toHaveBeenCalledWith("新条目.md", null);
    expect(onOpen).toHaveBeenCalledWith({
      channel: "document",
      path: "new",
      name: "新条目.md",
    });
  });

  it("passes folderId for a folder scope", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([]);
    vi.mocked(createRuleDocument).mockResolvedValue(
      detail({ id: "p", name: "新条目.md", folderId: "F1" }),
    );
    const onOpen = vi.fn();
    expect(
      await createAndOpenScopeEntry({ kind: "folder", folderId: "F1" }, onOpen),
    ).toBe(true);
    expect(listScopeEntries).toHaveBeenCalledWith("F1");
    expect(createRuleDocument).toHaveBeenCalledWith("新条目.md", "F1");
  });

  it("returns false and does not open when create fails", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([]);
    vi.mocked(createRuleDocument).mockRejectedValue(new Error("quota"));
    const onOpen = vi.fn();
    expect(await createAndOpenScopeEntry({ kind: "global" }, onOpen)).toBe(
      false,
    );
    expect(onOpen).not.toHaveBeenCalled();
    expect(notifyError).toHaveBeenCalled();
  });
});
