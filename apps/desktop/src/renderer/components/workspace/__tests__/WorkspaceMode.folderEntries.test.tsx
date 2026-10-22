// @vitest-environment jsdom
import { WorkspaceMode } from "@/components/workspace/WorkspacePanel";
import type { FileSource } from "@/lib/fileSource";
import { useConversationStore } from "@/stores/conversation";
import { WORKSPACE_TAB_ID, useSidePanelStore } from "@/stores/sidePanel";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const convs = vi.hoisted(() => ({
  list: [] as Array<{ id: string; folderId: string | null }>,
}));

vi.mock("@/hooks/useConversations", () => ({
  useConversations: () => convs.list,
  getConversations: () => convs.list,
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
  hasLocalFiles: () => false,
}));

vi.mock("@/hooks/useExternalGrants", () => ({
  useExternalGrants: () => ({ data: [], isError: false, refetch: vi.fn() }),
  useRevokeExternalGrant: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/components/workspace/FileBrowser", () => ({
  FileBrowser: ({
    renderWorkroomLead,
  }: {
    renderWorkroomLead?: (indent: number) => import("react").ReactNode;
  }) => (
    <div>
      {renderWorkroomLead?.(0)}
      <div data-testid="file-browser" />
    </div>
  ),
}));

vi.mock("@/components/workspace/ExternalMountsSection", () => ({
  ExternalMountsSection: () => null,
}));

vi.mock("@/components/files/fileWorkbench/EntriesSection", () => ({
  EntriesSection: ({
    onOpen,
    onDeleted,
  }: {
    onOpen: (t: { channel: "document"; path: string; name: string }) => void;
    onDeleted: (t: { channel: "document"; path: string; name: string }) => void;
  }) => {
    const target = {
      channel: "document" as const,
      path: "doc-1",
      name: "语气.md",
    };
    return (
      <div>
        <button type="button" onClick={() => onOpen(target)}>
          打开条目
        </button>
        <button type="button" onClick={() => onDeleted(target)}>
          删除条目
        </button>
      </div>
    );
  },
}));

afterEach(cleanup);

describe("WorkspaceMode · 文件树不挂文件夹规矩", () => {
  beforeEach(() => {
    convs.list = [];
    useConversationStore.setState({ currentConversationId: "c1" });
    useSidePanelStore.setState({
      open: true,
      tabs: [],
      activeTabId: WORKSPACE_TAB_ID,
      floats: [],
      focusSurface: { type: "dock" },
    });
  });

  it("绑定 folderId 时也不挂文件夹条目", () => {
    convs.list = [{ id: "c1", folderId: "f1" }];
    render(<WorkspaceMode />);
    expect(screen.queryByRole("button", { name: "打开条目" })).toBeNull();
    expect(screen.queryByText("全局设定")).toBeNull();
    expect(screen.getByTestId("file-browser")).toBeTruthy();
  });

  it("裸聊（无 folderId）不挂条目", () => {
    convs.list = [{ id: "c1", folderId: null }];
    render(<WorkspaceMode />);
    expect(screen.queryByRole("button", { name: "打开条目" })).toBeNull();
    expect(screen.queryByText("全局设定")).toBeNull();
    expect(screen.getByTestId("file-browser")).toBeTruthy();
  });
});
