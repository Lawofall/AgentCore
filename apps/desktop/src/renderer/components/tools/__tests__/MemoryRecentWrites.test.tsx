// @vitest-environment jsdom
import { formatMemoryTime } from "@/components/memory/MemoryUpdateItemRow";
import type { MemoryUpdateFeedEntry } from "@/services/memory";
import type { MemoryUpdateItem } from "@/stores/conversation";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRecentWrites } from "../MemoryRecentWrites";

vi.mock("@/services/memory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/memory")>();
  return {
    ...actual,
    listMemoryUpdates: vi.fn(async () => []),
  };
});

const { listMemoryUpdates } = await import("@/services/memory");

function item(
  partial: Partial<MemoryUpdateItem> &
    Pick<MemoryUpdateItem, "file" | "content">,
): MemoryUpdateItem {
  return {
    action: "add",
    section: "关于用户的事实",
    scope: "global",
    target: "",
    ...partial,
  };
}

function entry(
  partial: Partial<MemoryUpdateFeedEntry> & { items: MemoryUpdateItem[] },
): MemoryUpdateFeedEntry {
  return {
    id: "u1",
    conversationId: "c1",
    createdAt: "2026-09-01T12:00:00Z",
    kind: "semantic",
    summary: null,
    ...partial,
  };
}

beforeEach(() => {
  vi.mocked(listMemoryUpdates).mockReset();
  vi.mocked(listMemoryUpdates).mockResolvedValue([]);
});

afterEach(cleanup);

describe("MemoryRecentWrites", () => {
  it("筛不出条目时不渲染", async () => {
    vi.mocked(listMemoryUpdates).mockResolvedValue([
      entry({
        items: [
          item({
            file: "画像",
            content: "倾向使用 bun",
            target: "global/profile",
          }),
          item({
            file: "主题·笔记",
            section: "要点",
            content: "主题备忘",
            target: "global/topics/笔记",
          }),
        ],
      }),
    ]);
    const { container } = render(
      <MemoryRecentWrites memoryKind="preferences" />,
    );
    await waitFor(() => expect(listMemoryUpdates).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
    expect(screen.queryByText("倾向使用 bun")).toBeNull();
    expect(screen.queryByText("最近写入")).toBeNull();
    expect(screen.queryByTestId("memory-recent-writes")).toBeNull();
  });

  it("请求失败时不渲染、不报错", async () => {
    vi.mocked(listMemoryUpdates).mockRejectedValue(new Error("boom"));
    const { container } = render(<MemoryRecentWrites memoryKind="profile" />);
    await waitFor(() => expect(listMemoryUpdates).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
    expect(screen.queryByText(/失败/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("显示写向该文件的最近三条：动作、摘要、时间", async () => {
    const older = "2026-08-01T08:00:00Z";
    const newer = "2026-09-01T12:00:00Z";
    vi.mocked(listMemoryUpdates).mockResolvedValue([
      entry({
        id: "new",
        createdAt: newer,
        items: [
          item({
            file: "画像",
            content: "最新一条",
            target: "global/profile",
          }),
          item({
            action: "update",
            file: "偏好",
            section: "沟通偏好",
            content: "偏好不应出现",
            target: "global/preferences",
          }),
          item({
            action: "update",
            file: "画像",
            content: "次新一条",
            target: "global/profile",
          }),
        ],
      }),
      entry({
        id: "old",
        createdAt: older,
        items: [
          item({
            action: "remove",
            file: "画像.md",
            content: "第三旧",
            target: "global/profile",
          }),
          item({
            file: "画像",
            content: "不应出现的第四条",
            target: "global/profile",
          }),
        ],
      }),
    ]);
    render(<MemoryRecentWrites memoryKind="profile" />);
    expect(await screen.findByTestId("memory-recent-writes")).toBeTruthy();
    expect(screen.getByText("新增")).toBeTruthy();
    expect(screen.getByText("更新")).toBeTruthy();
    expect(screen.getByText("移除")).toBeTruthy();
    expect(screen.getByText("最新一条")).toBeTruthy();
    expect(screen.getByText("次新一条")).toBeTruthy();
    expect(screen.getByText("第三旧")).toBeTruthy();
    expect(screen.queryByText("不应出现的第四条")).toBeNull();
    expect(screen.queryByText("偏好不应出现")).toBeNull();
    expect(screen.getAllByText(formatMemoryTime(newer))).toHaveLength(2);
    expect(screen.getByText(formatMemoryTime(older))).toBeTruthy();
  });

  it("卸载后不 setState", async () => {
    let resolve!: (value: MemoryUpdateFeedEntry[]) => void;
    vi.mocked(listMemoryUpdates).mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const { unmount, container } = render(
      <MemoryRecentWrites memoryKind="profile" />,
    );
    unmount();
    await act(async () => {
      resolve([
        entry({
          items: [
            item({
              file: "画像",
              content: "卸载后这条不该出现",
              target: "global/profile",
            }),
          ],
        }),
      ]);
      await Promise.resolve();
    });
    expect(container.firstChild).toBeNull();
    expect(screen.queryByText("卸载后这条不该出现")).toBeNull();
  });
});
