// @vitest-environment jsdom
import { formatMemoryTime } from "@/components/memory/MemoryUpdateItemRow";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryUpdateCard } from "../MemoryUpdateCard";

const navigate = vi.fn();

let disclosureOpen = true;
const setDisclosureOpen = vi.fn(
  (updater: boolean | ((v: boolean) => boolean)) => {
    disclosureOpen =
      typeof updater === "function" ? updater(disclosureOpen) : updater;
  },
);

vi.mock("@/stores/disclosure", () => ({
  usePersistentDisclosure: () => [disclosureOpen, setDisclosureOpen],
}));

vi.mock("@/hooks/useConversations", () => ({
  getConversations: () => [{ id: "c1", folderId: "F99", title: "t" }],
}));

vi.mock("@/hooks/useFolders", () => ({
  getFolders: () => [{ id: "F99", name: "白板" }],
}));

vi.mock("@/stores/conversation", async () => {
  const actual = await vi.importActual<typeof import("@/stores/conversation")>(
    "@/stores/conversation",
  );
  return {
    ...actual,
    useConversationStore: (
      sel: (s: { currentConversationId: string }) => unknown,
    ) => sel({ currentConversationId: "c1" }),
  };
});

vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useNavigate: () => navigate,
  };
});

describe("MemoryUpdateCard", () => {
  beforeEach(() => {
    navigate.mockClear();
    disclosureOpen = true;
    setDisclosureOpen.mockClear();
  });

  it("labels the card with its anchor time, not the (later) consolidation write time", () => {
    const anchorAt = "2026-07-19T12:00:00Z";
    const createdAt = "2026-07-19T12:05:00Z";
    render(
      <MemoryRouter>
        <MemoryUpdateCard
          update={{
            id: "s-anchored",
            createdAt,
            anchorAt,
            kind: "semantic",
            items: [
              {
                action: "add",
                file: "画像",
                section: "关于用户的事实",
                scope: "global",
                content: "倾向使用 bun",
                target: "global/profile",
              },
            ],
          }}
        />
      </MemoryRouter>,
    );
    // 卡片插在 anchorAt 那一轮末尾，显示落库时刻会比它下方的消息还晚，读起来是乱序。
    expect(screen.getByText(formatMemoryTime(anchorAt))).toBeTruthy();
    expect(screen.queryByText(formatMemoryTime(createdAt))).toBeNull();
  });

  it("renders semantic diff card with scope overview and project pill", () => {
    render(
      <MemoryRouter>
        <MemoryUpdateCard
          update={{
            id: "s1",
            createdAt: "2026-07-19T12:00:00Z",
            kind: "semantic",
            items: [
              {
                action: "add",
                file: "画像",
                section: "关于用户的事实",
                scope: "global",
                content: "倾向使用 bun",
                target: "global/profile",
              },
              {
                action: "add",
                file: "画像",
                section: "技术栈与工具",
                scope: "project",
                content: "本项目用 Vite",
                target: "project/F99/profile",
                projectId: "F99",
              },
            ],
          }}
        />
      </MemoryRouter>,
    );
    expect(
      screen.getByText(/记忆已更新 · 全局 \+ 本文件夹 · 白板/),
    ).toBeTruthy();
    expect(screen.getByText("2 项")).toBeTruthy();
    expect(screen.getByText("本文件夹 · 白板")).toBeTruthy();
    expect(screen.getByText("移到本文件夹")).toBeTruthy();
    expect(screen.getByText("移到全局")).toBeTruthy();
  });

  it("quota card names denied entries and holders, hiding the fingerprint row", () => {
    render(
      <MemoryRouter>
        <MemoryUpdateCard
          update={{
            id: "q1",
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
          }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText(/常驻条目已满（120\/80 字符）/)).toBeTruthy();
    // The dedup fingerprint is backend bookkeeping — it must never reach the user,
    // and must not be counted in the 「N 项」 pill either.
    expect(screen.queryByText("fp-hash-must-not-render")).toBeNull();
    expect(screen.getByText("2 项")).toBeTruthy();
    expect(screen.getByText("未写入")).toBeTruthy();
    expect(screen.getByText("这次的更新没能写入常驻（40 字符）")).toBeTruthy();
    expect(screen.getByText("占用")).toBeTruthy();
    expect(screen.getByText("占用 100 字符")).toBeTruthy();
    // Quota rows report pool state, so they never offer 搬层.
    expect(screen.queryByText("移到本文件夹")).toBeNull();
    expect(screen.queryByText("移到全局")).toBeNull();
  });

  it("renders a fingerprint-only quota card without an empty item list", () => {
    render(
      <MemoryRouter>
        <MemoryUpdateCard
          update={{
            id: "q2",
            createdAt: "2026-07-19T12:00:00Z",
            kind: "quota",
            summary: "常驻条目已满（120/80 字符）。",
            items: [
              {
                action: "quota",
                file: "",
                section: "",
                scope: "global",
                content: "fp-only",
                target: "",
              },
            ],
          }}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText(/常驻条目已满/)).toBeTruthy();
    expect(screen.queryByText("1 项")).toBeNull();
    expect(screen.queryByText("fp-only")).toBeNull();
  });

  it("falls back to projectId when target does not encode folderId", () => {
    render(
      <MemoryRouter>
        <MemoryUpdateCard
          update={{
            id: "s2",
            createdAt: "2026-07-19T12:00:00Z",
            kind: "semantic",
            items: [
              {
                action: "add",
                file: "画像",
                section: "关于用户的事实",
                scope: "project",
                content: "本项目用 React",
                target: "broken-target",
                projectId: "F99",
              },
            ],
          }}
        />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTitle("在设定中打开画像"));
    expect(navigate).toHaveBeenCalledWith("/files", {
      state: {
        openMemoryLeaf: {
          path: "broken-target",
          name: "画像.md",
          projectId: "F99",
        },
        focusWsId: "folder:F99",
      },
    });
  });
});
