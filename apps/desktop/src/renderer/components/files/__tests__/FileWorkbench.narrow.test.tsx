// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/narrowLayout", () => ({
  useNarrowLayoutState: () => ({
    isNarrow: true,
    hideChrome: false,
    conversationDrawerOpen: false,
    setConversationDrawerOpen: () => undefined,
  }),
}));

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

import { FileWorkbench } from "@/components/files/FileWorkbench";

describe("FileWorkbench narrow", () => {
  afterEach(cleanup);

  it("does not keep an empty detail pane beside the tree", () => {
    render(
      <FileWorkbench
        workspaces={[]}
        isLoading={false}
        isError={false}
        onRetry={() => {}}
        fsAvailable={false}
      />,
    );
    expect(screen.queryByText("选择一个文件")).toBeNull();
    expect(screen.getByLabelText("按名称筛选文件夹或文件")).toBeTruthy();
  });
});
