// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import { WorkspaceMode } from "@/components/workspace/WorkspacePanel";
import type { FileSource } from "@/lib/fileSource";
import { useConversationStore } from "@/stores/conversation";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => [{ id: "c1", folderId: "f1" }],
  getConversations: () => [{ id: "c1", folderId: "f1" }],
}));

vi.mock("@/hooks/useConversationFileSource", () => ({
  useConversationFileSource: () =>
    ({
      id: "workspace:cloud",
      label: "云",
      caps: { watch: false, transfer: true, edit: true, snapshots: true },
    }) as FileSource,
}));

vi.mock("@/hooks/useWorkspaces", () => ({
  useConversationWorkspace: () => ({
    wsId: "folder:f1",
    name: "proj",
    location: "cloud" as const,
    rootId: null,
    subpath: "",
    hasFiles: true,
  }),
}));

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: () => true,
}));

vi.mock("@/hooks/useExternalGrants", () => ({
  useExternalGrants: () => ({ data: [], isError: false, refetch: vi.fn() }),
  useRevokeExternalGrant: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/components/workspace/FileBrowser", () => ({
  FileBrowser: ({ trailing }: { trailing?: import("react").ReactNode }) => (
    <div>
      {trailing}
      <div data-testid="file-browser" />
    </div>
  ),
}));

vi.mock("@/components/workspace/ExternalMountsSection", () => ({
  ExternalMountsSection: () => null,
}));

vi.mock("@/components/workspace/WorkspaceModeBar", () => ({
  WorkspaceModeBar: () => null,
}));

vi.mock("@/components/workspace/WorkspaceClientTools", () => ({
  WorkspaceClientTools: () => null,
}));

afterEach(cleanup);

describe("WorkspaceMode · 导出菜单", () => {
  beforeEach(() => {
    useConversationStore.setState({ currentConversationId: "c1" });
  });

  it("云桌工具条不再提供导出 ZIP / 到本机", () => {
    render(
      <TooltipProvider>
        <WorkspaceMode />
      </TooltipProvider>,
    );
    expect(screen.queryByRole("button", { name: "导出" })).toBeNull();
    expect(screen.queryByText("导出 ZIP")).toBeNull();
    expect(screen.queryByText("导出到本机文件夹")).toBeNull();
    expect(screen.getByRole("button", { name: "软删区" })).toBeTruthy();
  });
});
