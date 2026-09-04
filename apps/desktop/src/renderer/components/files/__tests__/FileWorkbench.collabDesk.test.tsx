// @vitest-environment jsdom
import { FileWorkbench } from "@/components/files/FileWorkbench";
import type { FolderMeta } from "@/services/folders";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const folders: FolderMeta[] = [
  {
    id: "own-1",
    name: "我的项目",
    mode: "cloud",
    localRootId: null,
    localSubpath: null,
  },
  {
    id: "shared-1",
    name: "队友桌",
    mode: "cloud",
    localRootId: null,
    localSubpath: null,
    myRole: "editor",
  },
];

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => [],
  getConversations: () => [],
}));

vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => folders,
  getFolders: () => folders,
}));

vi.mock("@/components/folders/PendingFolderInvites", () => ({
  PendingFolderInvites: () => null,
}));

vi.mock("@/components/files/fileWorkbench/WorkspaceSection", () => ({
  WorkspaceSection: ({ ws }: { ws: { name: string; wsId: string } }) => (
    <div data-testid={ws.wsId}>{ws.name}</div>
  ),
}));

describe("FileWorkbench · 协作桌", () => {
  afterEach(cleanup);

  it("owned desks stay in 我的文件; member desks are 与我共享; no shared-space zone", () => {
    render(
      <FileWorkbench
        workspaces={[]}
        isLoading={false}
        isError={false}
        onRetry={() => {}}
        fsAvailable={false}
      />,
    );

    expect(screen.getByText("我的文件")).toBeTruthy();
    expect(screen.getByText("与我共享")).toBeTruthy();
    expect(screen.getByText("我的项目")).toBeTruthy();
    expect(screen.getByText("队友桌")).toBeTruthy();
    expect(screen.queryByText("共享空间")).toBeNull();
    expect(screen.queryByText("挂载共享")).toBeNull();
    expect(screen.queryByText("还没有共享空间")).toBeNull();
    expect(screen.queryByLabelText("新建共享空间")).toBeNull();
    expect(screen.queryByLabelText("挂载共享空间")).toBeNull();
  });
});
