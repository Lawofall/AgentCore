import type { FileSource } from "@/lib/fileSource";
import { canExportMarkdown } from "@/lib/markdownExport";
import { describe, expect, it, vi } from "vitest";

function stub(overrides: Partial<FileSource> = {}): FileSource {
  return {
    id: "workspace:export",
    label: "工作区",
    caps: { watch: false, transfer: true, edit: true, snapshots: true },
    listDir: async () => [],
    read: async () => ({ kind: "text", text: "", truncated: false }),
    createFile: async () => {},
    mkdir: async () => {},
    move: async () => {},
    delete: async () => {},
    ...overrides,
  };
}

describe("canExportMarkdown", () => {
  it("可写源 + .md + 转换器才放行", () => {
    const source = stub({ exportMarkdown: vi.fn() });
    expect(canExportMarkdown(source, "起诉状.md")).toBe(true);
    expect(canExportMarkdown(source, "note.markdown")).toBe(true);
  });

  it("非 Markdown / 只读 / 无转换器都不挂入口", () => {
    const live = stub({ exportMarkdown: vi.fn() });
    expect(canExportMarkdown(live, "a.txt")).toBe(false);
    expect(
      canExportMarkdown(
        stub({
          caps: { watch: false, transfer: true, edit: false, snapshots: false },
          exportMarkdown: vi.fn(),
        }),
        "a.md",
      ),
    ).toBe(false);
    expect(canExportMarkdown(stub(), "a.md")).toBe(false);
  });
});
