// @vitest-environment jsdom
import { FileWorkbench } from "@/components/files/FileWorkbench";
import { TooltipProvider } from "@/components/ui/tooltip";
import { queryClient } from "@/lib/queryClient";
import { workspaceKeys } from "@/lib/queryKeys";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => [],
  getConversations: () => [],
}));

vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => [],
  getFolders: () => [],
  useCreateFolder: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/components/folders/PendingFolderInvites", () => ({
  PendingFolderInvites: () => null,
}));

describe("FileWorkbench mount", () => {
  afterEach(cleanup);

  function hub(node: ReactElement) {
    return render(<TooltipProvider>{node}</TooltipProvider>);
  }

  it("does not invalidate workspace list on open", () => {
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    hub(
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
    hub(
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
    expect(screen.getByText("我的文件")).toBeTruthy();
    expect(
      screen.getAllByRole("button", { name: "新建文件夹" }).length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("共享空间")).toBeNull();
    expect(screen.queryByText("挂载共享")).toBeNull();
    expect(screen.queryByText("还没有共享空间")).toBeNull();
    expect(screen.queryByLabelText("新建共享空间")).toBeNull();
  });

  it("does not pin account prompts or 最近更新 on the files rail", () => {
    hub(
      <MemoryRouter>
        <FileWorkbench
          workspaces={[]}
          isLoading={false}
          isError={false}
          onRetry={() => {}}
          fsAvailable={false}
        />
      </MemoryRouter>,
    );
    expect(screen.queryByText("全局设定")).toBeNull();
    expect(screen.queryByText("最近更新")).toBeNull();
    expect(
      screen.queryByRole("link", {
        name: "所有对话共用的提示词在工具箱",
      }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "新建条目" })).toBeNull();
  });
});
