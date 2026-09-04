import { FileWorkbench } from "@/components/files/FileWorkbench";
import { queryClient } from "@/lib/queryClient";
import { workspaceKeys } from "@/lib/queryKeys";
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => [],
  getConversations: () => [],
}));

vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => [],
  getFolders: () => [],
}));

vi.mock("@/components/folders/PendingFolderInvites", () => ({
  PendingFolderInvites: () => null,
}));

describe("FileWorkbench mount", () => {
  afterEach(cleanup);

  it("does not invalidate workspace list on open", () => {
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    render(
      <FileWorkbench
        workspaces={[]}
        isLoading={false}
        isError={false}
        onRetry={() => {}}
        fsAvailable={false}
      />,
    );
    expect(spy).not.toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: workspaceKeys.list }),
    );
    spy.mockRestore();
  });

  it("does not list 快速对话 even when conv: scratch is in the workspace list", () => {
    render(
      <FileWorkbench
        workspaces={[
          {
            wsId: "conv:c1",
            name: "一次快速对话",
            location: "cloud",
            rootId: null,
            subpath: "",
            hasFiles: true,
          },
        ]}
        isLoading={false}
        isError={false}
        onRetry={() => {}}
        fsAvailable={false}
      />,
    );
    expect(screen.queryByText("快速对话")).toBeNull();
    expect(screen.queryByText("快速对话产生文件后会出现在这里")).toBeNull();
    expect(screen.queryByText("一次快速对话")).toBeNull();
    expect(screen.getByText("还没有文件夹")).toBeTruthy();
    expect(screen.queryByText("共享空间")).toBeNull();
    expect(screen.queryByText("挂载共享")).toBeNull();
    expect(screen.queryByText("还没有共享空间")).toBeNull();
    expect(screen.queryByLabelText("新建共享空间")).toBeNull();
    expect(screen.getByText(/与我共享/)).toBeTruthy();
  });
});
