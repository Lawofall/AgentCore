// @vitest-environment jsdom
/**
 * 抽屉「最近删除」列表 / 恢复，以及活列表分享 sheet。
 */
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const {
  listConversations,
  listConversationsGrouped,
  listConversationTrash,
  restoreConversation,
} = vi.hoisted(() => ({
  listConversations: vi.fn(),
  listConversationsGrouped: vi.fn(),
  listConversationTrash: vi.fn(),
  restoreConversation: vi.fn(),
}));

const navigate = vi.fn();

vi.mock("@/api/client", () => ({ getTokens: () => ({ access: "token" }) }));
vi.mock("@/api/conversations", () => ({
  listConversations,
  listConversationsGrouped,
  listConversationTrash,
  deleteConversation: vi.fn(),
  renameConversation: vi.fn(),
  setConversationArchived: vi.fn(),
  setConversationPinned: vi.fn(),
  restoreConversation,
}));
vi.mock("@/api/search", () => ({ search: vi.fn() }));
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return { ...actual, useNavigate: () => navigate };
});
vi.mock("@/components/Modal", () => ({
  Modal: ({
    children,
    className,
    label,
  }: {
    children: ReactNode;
    className?: string;
    label?: string;
  }) => (
    <dialog className={className} aria-label={label} open>
      {children}
    </dialog>
  ),
}));
vi.mock("@/components/ShareConversationSheet", () => ({
  ShareConversationSheet: ({
    conversationId,
    title,
    onClose,
  }: {
    conversationId: string;
    title?: string | null;
    onClose: () => void;
  }) => (
    <dialog open aria-label="分享对话">
      <span>
        分享「{title}」{conversationId}
      </span>
      <button type="button" onClick={onClose}>
        关闭分享
      </button>
    </dialog>
  ),
}));

import type {
  ConversationSummary,
  ConversationTrash,
  DeletedConversationSummary,
  GroupedConversations,
} from "@/api/conversations";
import {
  __resetConversationListCacheForTests,
  getConversationListArchived,
  getConversationListGrouped,
} from "@/lib/conversationListCache";
import { ConversationDrawer } from "../ConversationDrawer";

function conv(over: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    id: "conv-1",
    title: "部署上线",
    archived: false,
    context_compacted: false,
    created_at: "2026-08-01T00:00:00Z",
    deep_research_auto: false,
    message_count: 3,
    pinned: false,
    updated_at: "2026-08-01T00:00:00Z",
    ...over,
  };
}

function deleted(
  over: Partial<DeletedConversationSummary> = {},
): DeletedConversationSummary {
  return {
    id: "del-1",
    title: "定价讨论",
    folder_id: null,
    message_count: 8,
    created_at: "2026-07-01T00:00:00Z",
    deleted_at: "2026-08-10T09:00:00Z",
    purge_at: "2026-09-09T09:00:00Z",
    ...over,
  };
}

function trash(over: Partial<ConversationTrash> = {}): ConversationTrash {
  return {
    items: [deleted()],
    retention_days: 30,
    total: 1,
    ...over,
  };
}

function grouped(
  over: Partial<GroupedConversations> = {},
): GroupedConversations {
  return { folders: [], ungrouped: [], ...over };
}

function renderDrawer() {
  return render(
    <MemoryRouter>
      <ConversationDrawer open onClose={() => {}} onOpen={() => {}} />
    </MemoryRouter>,
  );
}

function rowOf(title: string): HTMLElement {
  const row = screen.getByText(title).closest(".conv-row");
  if (!row) throw new Error(`没有找到「${title}」这一行`);
  return row as HTMLElement;
}

async function openTrash(title = "定价讨论") {
  await screen.findByText("部署上线");
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "最近删除" }));
  });
  await screen.findByText(title);
}

beforeEach(() => {
  navigate.mockReset();
  listConversations.mockReset();
  listConversationsGrouped.mockReset();
  listConversationTrash.mockReset();
  restoreConversation.mockReset();
  listConversations.mockResolvedValue([]);
  listConversationsGrouped.mockResolvedValue(grouped({ ungrouped: [conv()] }));
  listConversationTrash.mockResolvedValue(trash());
  restoreConversation.mockResolvedValue(
    conv({ id: "del-1", title: "定价讨论" }),
  );
  __resetConversationListCacheForTests();
});

afterEach(() => {
  cleanup();
  __resetConversationListCacheForTests();
});

describe("ConversationDrawer · 最近删除", () => {
  it("活列表出已归档和最近删除；打开 trash 才拉 listConversationTrash", async () => {
    renderDrawer();
    await screen.findByText("部署上线");
    expect(screen.getByRole("button", { name: "已归档" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "最近删除" })).toBeTruthy();
    expect(listConversationTrash).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "最近删除" }));
    });
    await screen.findByText("定价讨论");
    expect(listConversationTrash).toHaveBeenCalledTimes(1);
    expect(listConversations).not.toHaveBeenCalled();
  });

  it("子视图返回对话可跳另一子视图", async () => {
    listConversations.mockResolvedValue([
      conv({ id: "arch-1", title: "旧归档", archived: true }),
    ]);
    renderDrawer();
    await screen.findByText("部署上线");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "已归档" }));
    });
    await screen.findByText("旧归档");
    expect(screen.getByRole("button", { name: "返回对话" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "最近删除" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "已归档" })).toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "最近删除" }));
    });
    await screen.findByText("定价讨论");
    expect(screen.getByRole("button", { name: "返回对话" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "已归档" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "最近删除" })).toBeNull();
  });

  it("最近删除行不可点进对话，只能恢复，无彻底删除", async () => {
    renderDrawer();
    await openTrash();

    fireEvent.click(screen.getByText("定价讨论"));
    expect(navigate).not.toHaveBeenCalled();
    expect(
      within(rowOf("定价讨论")).getByRole("button", { name: "恢复" }),
    ).toBeTruthy();
    expect(screen.queryByLabelText("更多操作")).toBeNull();
    expect(screen.queryByRole("button", { name: "删除" })).toBeNull();
    expect(screen.queryByText(/彻底删除|永久删除/)).toBeNull();
  });

  it("恢复活对话 insertRestored，并从 trash 列表摘掉", async () => {
    renderDrawer();
    await openTrash();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "恢复" }));
    });
    await waitFor(() => {
      expect(restoreConversation).toHaveBeenCalledWith("del-1");
    });
    await waitFor(() => {
      expect(screen.queryByText("定价讨论")).toBeNull();
    });
    expect(
      getConversationListGrouped()?.ungrouped.some((c) => c.id === "del-1"),
    ).toBe(true);
  });

  it("恢复仍归档的 replaceArchived 插回，并从 trash 列表摘掉", async () => {
    const archived = conv({
      id: "del-arch",
      title: "归档过的",
      archived: true,
    });
    listConversationTrash.mockResolvedValue(
      trash({
        items: [deleted({ id: "del-arch", title: "归档过的" })],
      }),
    );
    restoreConversation.mockResolvedValue(archived);
    renderDrawer();
    await openTrash("归档过的");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "恢复" }));
    });
    await waitFor(() => {
      expect(restoreConversation).toHaveBeenCalledWith("del-arch");
    });
    await waitFor(() => {
      expect(screen.queryByText("归档过的")).toBeNull();
    });
    expect(
      getConversationListArchived()?.some((c) => c.id === "del-arch"),
    ).toBe(true);
  });

  it("恢复 409 把错误亮出来，行还在", async () => {
    restoreConversation.mockRejectedValue(
      new Error("该对话已被清理，无法恢复"),
    );
    renderDrawer();
    await openTrash();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "恢复" }));
    });
    await screen.findByText("该对话已被清理，无法恢复");
    expect(screen.getByText("定价讨论")).toBeTruthy();
  });

  it("空态提保留期", async () => {
    listConversationTrash.mockResolvedValue(
      trash({ items: [], retention_days: 30, total: 0 }),
    );
    renderDrawer();
    await screen.findByText("部署上线");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "最近删除" }));
    });
    await screen.findByText(/最近删除是空的/);
    expect(screen.getByText(/保留 30 天/)).toBeTruthy();
  });
});

describe("ConversationDrawer · 分享", () => {
  it("活列表 ActionSheet 能开 ShareConversationSheet", async () => {
    renderDrawer();
    await screen.findByText("部署上线");
    fireEvent.click(within(rowOf("部署上线")).getByLabelText("更多操作"));
    fireEvent.click(screen.getByRole("button", { name: "分享" }));
    expect(screen.getByLabelText("分享对话").textContent).toContain("部署上线");
    expect(screen.getByLabelText("分享对话").textContent).toContain("conv-1");
  });

  it("已归档 ActionSheet 不分享", async () => {
    listConversations.mockResolvedValue([
      conv({ id: "arch-1", title: "旧归档", archived: true }),
    ]);
    renderDrawer();
    await screen.findByText("部署上线");
    fireEvent.click(screen.getByRole("button", { name: "已归档" }));
    await screen.findByText("旧归档");
    fireEvent.click(within(rowOf("旧归档")).getByLabelText("更多操作"));
    expect(screen.queryByRole("button", { name: "分享" })).toBeNull();
    expect(screen.queryByLabelText("分享对话")).toBeNull();
  });
});
