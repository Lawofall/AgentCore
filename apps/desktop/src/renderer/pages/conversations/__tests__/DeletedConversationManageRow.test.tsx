// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import type { DeletedConversationMeta } from "@/services/conversations";
import type { FolderMeta } from "@/services/folders";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DeletedConversationManageRow } from "../DeletedConversationManageRow";

const restore = vi.fn();
let folders: FolderMeta[] = [];

vi.mock("@/hooks/useConversations", () => ({
  useRestoreConversation: () => ({ mutate: restore, isPending: false }),
}));
vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => folders,
}));

function deleted(
  overrides: Partial<DeletedConversationMeta> = {},
): DeletedConversationMeta {
  return {
    id: "c1",
    title: "定价讨论",
    folderId: "f1",
    messageCount: 24,
    deletedAt: new Date(Date.now() - 3_600_000).toISOString(),
    // Half a day of slack: the label floors, so an exact multiple would read as one
    // day less by the time the row renders.
    purgeAt: new Date(Date.now() + 29.5 * 86_400_000).toISOString(),
    ...overrides,
  };
}

function renderRow(overrides: Partial<DeletedConversationMeta> = {}) {
  return render(
    <TooltipProvider>
      <DeletedConversationManageRow conversation={deleted(overrides)} />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  restore.mockReset();
  folders = [
    {
      id: "f1",
      name: "商标案",
      mode: "cloud",
      localRootId: null,
      localSubpath: null,
    },
  ];
});
afterEach(cleanup);

describe("DeletedConversationManageRow", () => {
  it("names the project the chat will return to, and its remaining window", () => {
    renderRow();

    expect(screen.getByText("定价讨论")).toBeTruthy();
    expect(screen.getByText("商标案")).toBeTruthy();
    expect(screen.getByText("剩 29 天")).toBeTruthy();
    expect(screen.queryByText("原文件夹也已删除")).toBeNull();
  });

  it("restores the chat the row is for", () => {
    renderRow();

    fireEvent.click(screen.getByLabelText("恢复对话 定价讨论"));

    expect(restore).toHaveBeenCalledWith("c1");
  });

  it("admits it lands in 快速对话 when the project was deleted too", () => {
    // Restoring the chat alone leaves folder_id pointing at a soft-deleted project,
    // which reads as 快速对话 — saying「回到原来的位置」here would be the lie all over
    // again, just one level up.
    folders = [];

    renderRow();

    expect(screen.getByText("原文件夹也已删除")).toBeTruthy();
    expect(screen.getByText(/恢复后先回到快速对话/)).toBeTruthy();
  });

  it("says nothing about projects for a 裸聊", () => {
    renderRow({ folderId: null });

    expect(screen.queryByText("原文件夹也已删除")).toBeNull();
    expect(screen.queryByText("商标案")).toBeNull();
  });
});
