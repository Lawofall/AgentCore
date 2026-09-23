// @vitest-environment jsdom
/**
 * EntriesSection — flat AgentCore entries by scope (no 记忆/规则/文档 folders).
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError } from "@/services/api";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/documents", () => ({
  listScopeEntries: vi.fn(),
  createRuleDocument: vi.fn(),
  deleteDocument: vi.fn(),
  renameDocument: vi.fn(),
  updateDocumentApplyMode: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
  notifyWarning: vi.fn(),
}));

import {
  type DocumentNode,
  deleteDocument,
  listScopeEntries,
} from "@/services/documents";
import { EntriesSection, entryOpenTarget } from "../EntriesSection";

const entry = (over: Partial<DocumentNode> = {}): DocumentNode => ({
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
  alwaysChars: over.applyMode === "on_demand" ? null : 1200,
  ...over,
});

function renderScope(scope: "global" | "folder" = "global") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0, gcTime: 0 } },
  });
  const onOpen = vi.fn();
  const onDeleted = vi.fn();
  const onRenamed = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <EntriesSection
          scope={
            scope === "global"
              ? { kind: "global" }
              : { kind: "folder", folderId: "F1" }
          }
          documentActivePath={null}
          onOpen={onOpen}
          onDeleted={onDeleted}
          onRenamed={onRenamed}
        />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return { onOpen, onDeleted, onRenamed };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listScopeEntries).mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("entryOpenTarget", () => {
  it("routes every listed entry to a document id", () => {
    expect(entryOpenTarget(entry({ id: "d9", name: "语气.md" }))).toEqual({
      channel: "document",
      path: "d9",
      name: "语气.md",
    });
    expect(
      entryOpenTarget(
        entry({ id: "core", aiMaintained: true, name: "画像.md" }),
      ),
    ).toEqual({
      channel: "document",
      path: "core",
      name: "画像.md",
    });
  });
});

describe("EntriesSection (global)", () => {
  it("lists flat entries with description — no 记忆/规则 folders", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({
        id: "g1",
        name: "语气.md",
        applyMode: "always",
        description: "回复语气",
        alwaysChars: 1200,
      }),
      entry({
        id: "g2",
        name: "画像.md",
        aiMaintained: true,
        applyMode: "always",
        description: "用户画像",
        alwaysChars: 800,
      }),
      entry({
        id: "g3",
        name: "偶发.md",
        applyMode: "on_demand",
        description: "",
        alwaysChars: null,
      }),
    ]);
    renderScope("global");

    expect(await screen.findByText("语气.md")).toBeTruthy();
    expect(screen.getByText("回复语气")).toBeTruthy();
    expect(screen.getByText("用户画像")).toBeTruthy();
    expect(screen.getByText("偶发.md")).toBeTruthy();
    expect(screen.queryByText("偏好.md")).toBeNull();
    expect(screen.queryByText("常驻")).toBeNull();
    expect(screen.queryByText("按需")).toBeNull();
    expect(screen.queryByText("记忆")).toBeNull();
    expect(screen.queryByText("规则")).toBeNull();
    expect(screen.queryByText(/^文档$/)).toBeNull();
    expect(screen.queryByText(/千字|万字/)).toBeNull();
    expect(screen.queryByText(/还剩约/)).toBeNull();
    expect(screen.queryByLabelText("新建条目")).toBeNull();
    expect(screen.queryByText("最近更新")).toBeNull();
  });

  it("does not print a row size, including for always entries", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({ id: "g1", name: "语气.md", alwaysChars: 4200 }),
      entry({ id: "g2", name: "小规则.md", alwaysChars: 450 }),
      entry({
        id: "g3",
        name: "偏好.md",
        aiMaintained: true,
        alwaysChars: 0,
      }),
    ]);
    renderScope("global");

    expect(await screen.findByText("语气.md")).toBeTruthy();
    expect(screen.getByText("小规则.md")).toBeTruthy();
    expect(screen.queryByText(/千字|万字/)).toBeNull();
    expect(screen.queryByText("0 字")).toBeNull();
  });

  it("does not fetch always-quota and does not render a usage meter", async () => {
    renderScope("global");
    expect(await screen.findByText("还没有全局条目")).toBeTruthy();
    expect(screen.queryByText(/还剩约/)).toBeNull();
    expect(screen.queryByText(/快满了/)).toBeNull();
    expect(screen.queryByLabelText("新建条目")).toBeNull();
  });

  it("shows an empty hint when the scope has no documents yet", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([]);
    const { onOpen } = renderScope("global");
    expect(await screen.findByText("还没有全局条目")).toBeTruthy();
    expect(screen.queryByText("偏好.md")).toBeNull();
    expect(screen.queryByText("画像.md")).toBeNull();
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("does not expose apply_mode on leftover AI-maintained rows", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({
        id: "g2",
        name: "画像.md",
        aiMaintained: true,
        applyMode: "always",
      }),
    ]);
    renderScope("global");
    expect(await screen.findByText("画像.md")).toBeTruthy();
    expect(screen.queryByLabelText(/生效方式/)).toBeNull();
    expect(screen.queryByText("常驻")).toBeNull();
    expect(screen.queryByText("设为常驻")).toBeNull();
    expect(screen.queryByText("设为按需")).toBeNull();
  });

  it("surfaces frontmatter_error as 不生效", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({
        id: "bad",
        name: "坏.md",
        frontmatterError: "unclosed frontmatter",
      }),
    ]);
    renderScope("global");
    expect(await screen.findByText("不生效")).toBeTruthy();
    expect(screen.getByText("unclosed frontmatter")).toBeTruthy();
  });

  it("does not mark a disputed leftover as 已停用", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({
        id: "g1",
        name: "过时偏好.md",
        applyMode: "always",
        alwaysChars: null,
        disputedAt: "2026-07-19T12:00:00Z",
      }),
    ]);
    renderScope("global");

    const label = await screen.findByText("过时偏好.md");
    expect(label.className).not.toContain("line-through");
    expect(screen.queryByText("已停用")).toBeNull();
    expect(screen.queryByText(/千字|万字/)).toBeNull();
  });

  it("offers 删除 (not 清空) on leftover AI-maintained cores; no memory PUT", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({
        id: "g1",
        name: "偏好.md",
        aiMaintained: true,
        applyMode: "always",
      }),
    ]);
    vi.mocked(deleteDocument).mockResolvedValue({
      ok: true,
      version: "v",
      conflict: false,
      frontmatterError: null,
      quotaWarning: null,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { onDeleted } = renderScope("global");

    fireEvent.contextMenu(await screen.findByText("偏好.md"));
    expect(screen.queryByText("清空")).toBeNull();
    expect(screen.queryByText("这条不对…")).toBeNull();
    fireEvent.click(screen.getByText("删除"));
    await waitFor(() => expect(deleteDocument).toHaveBeenCalledWith("g1"));
    expect(onDeleted).toHaveBeenCalledWith({
      channel: "document",
      path: "g1",
      name: "偏好.md",
    });
  });

  it("does not offer 这条不对 on handwritten entries; delete remains", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({ id: "g1", name: "语气.md" }),
    ]);
    renderScope("global");

    fireEvent.contextMenu(await screen.findByText("语气.md"));
    expect(screen.queryByText("这条不对…")).toBeNull();
    expect(screen.getByText("删除")).toBeTruthy();
  });

  it("shows calm unavailable when documents API is missing", async () => {
    vi.mocked(listScopeEntries).mockRejectedValue(new ApiError(404, "missing"));
    renderScope("global");
    expect(await screen.findByText(/条目功能暂不可用/)).toBeTruthy();
  });

  it("条目列表加载失败 is muted, not destructive", async () => {
    vi.mocked(listScopeEntries).mockRejectedValue(new Error("list down"));
    renderScope("global");
    const btn = await screen.findByText("加载失败，点此重试");
    expect(btn.className).toContain("text-muted-foreground");
    expect(btn.className).not.toContain("destructive");
  });

  it("lists leftover topic names as ordinary rows, not a 主题 folder", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({
        id: "g1",
        name: "语气.md",
        applyMode: "always",
        description: "回复语气",
      }),
      entry({
        id: "t1",
        name: "主题/部署.md",
        aiMaintained: true,
        applyMode: "on_demand",
        description: "怎么发",
        alwaysChars: null,
      }),
    ]);
    renderScope("global");

    expect(await screen.findByText("语气.md")).toBeTruthy();
    expect(screen.getByText("主题/部署.md")).toBeTruthy();
    expect(screen.getByText("怎么发")).toBeTruthy();
    expect(screen.queryByText("主题 · 1")).toBeNull();
    expect(screen.queryByText("主题 · 2")).toBeNull();
  });
});

describe("EntriesSection (project)", () => {
  it("loads the project scope without empty 画像/导航 slots", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({
        id: "p1",
        folderId: "F1",
        name: "导航.md",
        aiMaintained: true,
        description: "项目路由",
        alwaysChars: 2400,
      }),
    ]);
    renderScope("folder");
    expect(await screen.findByText("导航.md")).toBeTruthy();
    expect(screen.getByText("项目路由")).toBeTruthy();
    expect(screen.queryByText("画像.md")).toBeNull();
    expect(listScopeEntries).toHaveBeenCalledWith("F1");
    expect(screen.queryByText(/千字|万字/)).toBeNull();
  });
});
