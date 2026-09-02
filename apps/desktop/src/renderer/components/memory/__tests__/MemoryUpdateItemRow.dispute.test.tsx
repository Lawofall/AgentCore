import type { MemoryUpdateItem } from "@/stores/conversation";
// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryUpdateItemRow } from "../MemoryUpdateItemRow";

/**
 * 纠错通道·行级 on the「记忆已更新」card: one click rejects the sentence the user is
 * looking at, targeting the layer that sentence lives in, and the toast carries 撤销.
 */

const disputeMemoryLine = vi.fn();
const restoreMemoryLine = vi.fn();
const notifyInfo = vi.fn();
const notifyActionError = vi.fn();

vi.mock("@/services/memory", () => ({
  disputeMemoryLine: (...args: unknown[]) => disputeMemoryLine(...args),
  restoreMemoryLine: (...args: unknown[]) => restoreMemoryLine(...args),
  moveMemoryBullet: vi.fn(),
}));

vi.mock("@/lib/toast", () => ({
  notifyInfo: (...args: unknown[]) => notifyInfo(...args),
  notifyActionError: (...args: unknown[]) => notifyActionError(...args),
}));

vi.mock("@/hooks/useFolders", () => ({
  getFolders: () => [{ id: "F1", name: "AgentCore" }],
}));

const globalItem: MemoryUpdateItem = {
  action: "add",
  file: "画像",
  section: "关于用户的事实",
  content: "用户在腾讯工作",
  target: "global/profile",
  scope: "global",
};

beforeEach(() => {
  vi.clearAllMocks();
  disputeMemoryLine.mockResolvedValue({
    ok: true,
    conflict: false,
    version: "v2",
    lineId: "rec-2",
  });
  restoreMemoryLine.mockResolvedValue({
    ok: true,
    conflict: false,
    version: "v3",
    lineId: "",
  });
});

describe("MemoryUpdateItemRow 这条不对", () => {
  it("rejects the exact line, in the layer it lives in", async () => {
    render(<MemoryUpdateItemRow item={globalItem} onOpenLeaf={() => {}} />);

    fireEvent.click(screen.getByText("这条不对"));

    await waitFor(() =>
      expect(disputeMemoryLine).toHaveBeenCalledWith({
        content: "用户在腾讯工作",
        section: "关于用户的事实",
        folderId: null,
        kind: "profile",
        topicSlug: null,
      }),
    );
  });

  it("targets the project layer for a project-scoped line", async () => {
    render(
      <MemoryUpdateItemRow
        item={{ ...globalItem, scope: "project", projectId: "F1" }}
        onOpenLeaf={() => {}}
      />,
    );

    fireEvent.click(screen.getByText("这条不对"));

    await waitFor(() =>
      expect(disputeMemoryLine).toHaveBeenCalledWith(
        expect.objectContaining({ folderId: "F1" }),
      ),
    );
  });

  it("offers an undo addressed by the record id, not its position", async () => {
    // Positions shift as records are dropped, so an index-addressed undo would put back
    // whatever line moved into that slot while the toast said it put back this one.
    render(<MemoryUpdateItemRow item={globalItem} onOpenLeaf={() => {}} />);
    fireEvent.click(screen.getByText("这条不对"));
    await waitFor(() => expect(notifyInfo).toHaveBeenCalled());

    const opts = notifyInfo.mock.calls[0]?.[1] as
      | { action?: { label: string; onClick: () => void } }
      | undefined;
    expect(opts?.action?.label).toBe("撤销");
    opts?.action?.onClick();

    await waitFor(() =>
      expect(restoreMemoryLine).toHaveBeenCalledWith({
        id: "rec-2",
        kind: "profile",
        topicSlug: null,
        folderId: null,
      }),
    );
  });

  it("offers no undo when the server handed back no record id", async () => {
    disputeMemoryLine.mockResolvedValue({
      ok: true,
      conflict: false,
      version: "v2",
      lineId: "",
    });
    render(<MemoryUpdateItemRow item={globalItem} onOpenLeaf={() => {}} />);
    fireEvent.click(screen.getByText("这条不对"));
    await waitFor(() => expect(notifyInfo).toHaveBeenCalled());

    const opts = notifyInfo.mock.calls[0]?.[1] as
      | { action?: { label: string } }
      | undefined;
    expect(opts?.action).toBeUndefined();
  });

  it("tells its host that memory changed, so other surfaces can refetch", async () => {
    const onMemoryChanged = vi.fn();
    render(
      <MemoryUpdateItemRow
        item={globalItem}
        onOpenLeaf={() => {}}
        onMemoryChanged={onMemoryChanged}
      />,
    );
    fireEvent.click(screen.getByText("这条不对"));
    await waitFor(() => expect(onMemoryChanged).toHaveBeenCalled());
  });

  it("never promises the AI will not learn it again", async () => {
    // The line stops being injected; nothing here stops a later conversation teaching the
    // same fact back. Wording that claims otherwise would be a promise we cannot keep —
    // 巩固侧至今没有消费任何 dispute 信号 (memory/dispute_line.py 诚实边界).
    render(<MemoryUpdateItemRow item={globalItem} onOpenLeaf={() => {}} />);
    fireEvent.click(screen.getByText("这条不对"));
    await waitFor(() => expect(notifyInfo).toHaveBeenCalled());

    const said = JSON.stringify(notifyInfo.mock.calls);
    expect(said).not.toMatch(/不会再|再也不|永远|以后都不|彻底删除/);
  });

  it("keeps quiet on rows that hold no remembered content", () => {
    render(
      <MemoryUpdateItemRow
        item={{ ...globalItem, action: "remove" }}
        onOpenLeaf={() => {}}
      />,
    );
    expect(screen.queryByText("这条不对")).toBeNull();
  });

  it("keeps 这条不对 outside the open-leaf button, so a click does not navigate", async () => {
    const onOpenLeaf = vi.fn();
    render(<MemoryUpdateItemRow item={globalItem} onOpenLeaf={onOpenLeaf} />);

    const dispute = screen.getByText("这条不对");
    const openLeaf = screen.getByTitle("在设定中打开画像");
    expect(openLeaf.contains(dispute)).toBe(false);

    fireEvent.click(dispute);
    expect(onOpenLeaf).not.toHaveBeenCalled();
    await waitFor(() => expect(disputeMemoryLine).toHaveBeenCalled());
  });
});
