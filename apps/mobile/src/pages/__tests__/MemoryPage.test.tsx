// @vitest-environment jsdom
/**
 * 全局设定：顶栏、最近更新空态、扁平条目（规则 + 偏好/画像占位）、
 * 新建条目、常驻用量、打开通道（偏好 → memory files，用户条目 → documents）。
 */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  getTokens: () => ({ access_token: "a", refresh_token: "r" }),
}));

vi.mock("@/api/memory", () => ({
  listMemoryUpdates: vi.fn(async () => []),
  getMemoryFile: vi.fn(async () => ({ content: "", version: "v1" })),
  writeMemoryFile: vi.fn(),
  writeMemoryTopic: vi.fn(),
  getMemoryTopic: vi.fn(),
  isFeatureUnavailable: () => false,
}));

vi.mock("@/api/documents", () => ({
  listScopeEntries: vi.fn(),
  getAlwaysQuota: vi.fn(),
  createRuleDocument: vi.fn(),
  getDocument: vi.fn(),
  writeDocument: vi.fn(),
  renameDocument: vi.fn(),
  deleteDocument: vi.fn(),
  updateDocumentApplyMode: vi.fn(),
  isDocumentsUnavailable: (e: unknown) =>
    e instanceof Error && e.message === "unavailable",
}));

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useNavigate: () => navigate,
    useLocation: () => ({ hash: "" }),
  };
});

import {
  createRuleDocument,
  getAlwaysQuota,
  getDocument,
  listScopeEntries,
} from "@/api/documents";
import { getMemoryFile, getMemoryTopic, listMemoryUpdates } from "@/api/memory";
import { MemoryPage } from "@/pages/MemoryPage";

const entry = (over: Record<string, unknown> = {}) => ({
  id: "e1",
  parentId: null,
  folderId: null,
  kind: "document" as const,
  role: "rule" as const,
  aiMaintained: false,
  applyMode: "always" as const,
  description: "",
  name: "e.md",
  frontmatterError: null,
  alwaysChars: null,
  disputedAt: null,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listMemoryUpdates).mockResolvedValue([]);
  vi.mocked(listScopeEntries).mockResolvedValue([]);
  vi.mocked(getAlwaysQuota).mockResolvedValue({
    usedChars: 4200,
    maxChars: 24000,
    percent: 17.5,
    globalChars: 4200,
    projectChars: 0,
  });
  vi.mocked(getMemoryFile).mockResolvedValue({ content: "", version: "v1" });
  vi.mocked(getDocument).mockResolvedValue({
    ...entry(),
    content: "",
    version: "v1",
  });
});

afterEach(cleanup);

describe("MemoryPage", () => {
  it("uses icon-btn back + centered bar-title", async () => {
    render(<MemoryPage />);
    expect(screen.getByLabelText("返回").className).toMatch(/icon-btn/);
    expect(document.querySelector(".bar-title")?.textContent).toBe("全局设定");
    expect(screen.queryByText("← 文件")).toBeNull();
    expect(await screen.findByText(/还没有记忆更新/)).toBeTruthy();
  });

  it("shows recent-updates empty copy", async () => {
    render(<MemoryPage />);
    expect(
      await screen.findByText(/还没有记忆更新。AI\s*会在对话后台整理长期记忆/),
    ).toBeTruthy();
  });

  it("lists a user rule alongside 偏好/画像 placeholders", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({
        id: "r1",
        name: "语气规则.md",
        description: "回复语气",
        alwaysChars: 1200,
      }),
    ]);
    render(<MemoryPage />);
    expect(await screen.findByText("语气规则.md")).toBeTruthy();
    expect(screen.getByText("回复语气")).toBeTruthy();
    expect(screen.getByText("偏好.md")).toBeTruthy();
    expect(screen.getByText("画像.md")).toBeTruthy();
    expect(listScopeEntries).toHaveBeenCalledWith(null);
  });

  it("新建条目 calls createRuleDocument", async () => {
    vi.mocked(createRuleDocument).mockResolvedValue({
      ...entry({ id: "new", name: "新条目.md" }),
      content: "",
      version: "v1",
    });
    render(<MemoryPage />);
    await screen.findByText("偏好.md");
    fireEvent.click(screen.getByText("新建条目"));
    await waitFor(() => {
      expect(vi.mocked(createRuleDocument).mock.calls[0]?.[0]).toBe(
        "新条目.md",
      );
    });
    expect(await screen.findByText("新条目.md")).toBeTruthy();
  });

  it("shows the global always-quota headline", async () => {
    render(<MemoryPage />);
    expect(await screen.findByText("常驻 · 还剩约 2 万字")).toBeTruthy();
    expect(getAlwaysQuota).toHaveBeenCalled();
  });

  it("opens 偏好 via getMemoryFile", async () => {
    vi.mocked(getMemoryFile).mockResolvedValue({
      content: "喜欢简洁",
      version: "v1",
    });
    render(<MemoryPage />);
    fireEvent.click(await screen.findByText("偏好.md"));
    await waitFor(() => {
      expect(getMemoryFile).toHaveBeenCalledWith("preferences");
    });
    expect(getDocument).not.toHaveBeenCalled();
    expect(await screen.findByDisplayValue("喜欢简洁")).toBeTruthy();
  });

  it("opens a user entry via getDocument", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({ id: "r1", name: "语气规则.md" }),
    ]);
    vi.mocked(getDocument).mockResolvedValue({
      ...entry({ id: "r1", name: "语气规则.md" }),
      content: "必须简洁",
      version: "v1",
    });
    render(<MemoryPage />);
    fireEvent.click(await screen.findByText("语气规则.md"));
    await waitFor(() => {
      expect(getDocument).toHaveBeenCalledWith("r1");
    });
    expect(getMemoryFile).not.toHaveBeenCalled();
    expect(await screen.findByDisplayValue("必须简洁")).toBeTruthy();
  });

  it("surfaces frontmatter errors on the row", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({
        id: "bad",
        name: "坏.md",
        frontmatterError: "unclosed frontmatter",
      }),
    ]);
    render(<MemoryPage />);
    expect(await screen.findByText("不生效")).toBeTruthy();
    expect(screen.getByText("unclosed frontmatter")).toBeTruthy();
  });

  it("documents 404/501 is a calm 暂不可用, not a red bar", async () => {
    vi.mocked(listScopeEntries).mockRejectedValue(new Error("unavailable"));
    render(<MemoryPage />);
    const note = await screen.findByText("暂不可用");
    expect(note.className).toMatch(/section-note/);
    expect(note.className).not.toMatch(/error/);
    expect(screen.getByText("偏好.md")).toBeTruthy();
    expect(screen.getByText("画像.md")).toBeTruthy();
  });

  it("near-full quota prompts 去整理", async () => {
    vi.mocked(getAlwaysQuota).mockResolvedValue({
      usedChars: 20000,
      maxChars: 24000,
      percent: 83.3,
      globalChars: 20000,
      projectChars: 0,
    });
    render(<MemoryPage />);
    expect(
      await screen.findByText("常驻 · 快满了，还剩约 4 千字"),
    ).toBeTruthy();
    expect(screen.getByText("去整理")).toBeTruthy();
  });

  it("hides always-char labels under 千字", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({ id: "r1", name: "语气规则.md", alwaysChars: 450 }),
      entry({ id: "r2", name: "长规则.md", alwaysChars: 1200 }),
    ]);
    render(<MemoryPage />);
    expect(await screen.findByText("约 1 千字")).toBeTruthy();
    expect(screen.queryByText("不足千字")).toBeNull();
  });

  it("opens 主题/… via getMemoryTopic", async () => {
    vi.mocked(listScopeEntries).mockResolvedValue([
      entry({
        id: "t1",
        name: "主题/部署.md",
        aiMaintained: true,
        applyMode: "on_demand",
      }),
    ]);
    vi.mocked(getMemoryTopic).mockResolvedValue({
      content: "部署笔记",
      version: "v1",
    });
    render(<MemoryPage />);
    fireEvent.click(await screen.findByText("主题/部署.md"));
    await waitFor(() => {
      expect(getMemoryTopic).toHaveBeenCalledWith("部署");
    });
    expect(getDocument).not.toHaveBeenCalled();
    expect(await screen.findByDisplayValue("部署笔记")).toBeTruthy();
  });

  it("renders a quota feed card without exposing the fingerprint row", async () => {
    vi.mocked(listMemoryUpdates).mockResolvedValue([
      {
        id: "q1",
        conversationId: "c1",
        createdAt: "2026-07-19T12:00:00Z",
        kind: "quota",
        summary: "常驻条目已满（120/80 字符）：以下 1 条没能写进常驻。",
        items: [
          {
            action: "quota",
            file: "",
            section: "",
            scope: "global",
            content: "fp-hash-must-not-render",
            target: "",
          },
          {
            action: "quota_denied",
            file: "画像",
            section: "",
            scope: "global",
            content: "这次的更新没能写入常驻（40 字符）",
            target: "global/profile",
          },
          {
            action: "quota_holder",
            file: "占坑规则.md",
            section: "",
            scope: "global",
            content: "占用 100 字符",
            target: "",
          },
        ],
      },
    ]);
    render(<MemoryPage />);
    expect(await screen.findByText("常驻已满")).toBeTruthy();
    expect(screen.getByText(/常驻条目已满（120\/80 字符）/)).toBeTruthy();
    expect(screen.queryByText("fp-hash-must-not-render")).toBeNull();
    expect(screen.getByText("未写入")).toBeTruthy();
    expect(screen.getByText("占用")).toBeTruthy();
  });
});
