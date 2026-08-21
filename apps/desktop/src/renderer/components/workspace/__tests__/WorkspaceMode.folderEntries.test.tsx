// @vitest-environment jsdom
import { WorkspaceMode } from "@/components/workspace/WorkspacePanel";
import type { FileSource } from "@/lib/fileSource";
import { useConversationStore } from "@/stores/conversation";
import {
  WORKSPACE_TAB_ID,
  fileTabId,
  useSidePanelStore,
} from "@/stores/sidePanel";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

vi.mock("@/components/workspace/SharedMountsSection", () => ({
  SharedMountsSection: () => null,
}));

vi.mock("@/components/files/fileWorkbench/EntriesSection", () => ({
  EntriesSection: ({
    onOpen,
    onDeleted,
  }: {
    onOpen: (t: { channel: "memory"; path: string; name: string }) => void;
    onDeleted: (t: { channel: "memory"; path: string; name: string }) => void;
  }) => {
    const target = {
      channel: "memory" as const,
      path: "project/f1/profile",
      name: "画像.md",
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

describe("WorkspaceMode · .agentcore 条目", () => {
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

  it("绑定 folderId 时挂条目，不挂「全局设定」", () => {
    convs.list = [{ id: "c1", folderId: "f1" }];
    render(<WorkspaceMode />);
    expect(screen.getByRole("button", { name: "打开条目" })).toBeTruthy();
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

  it("点条目开带通道的 File tab；删条目关掉对应 tab", () => {
    convs.list = [{ id: "c1", folderId: "f1" }];
    render(<WorkspaceMode />);
    fireEvent.click(screen.getByRole("button", { name: "打开条目" }));

    const opened = useSidePanelStore.getState().tabs[0];
    expect(opened).toMatchObject({
      kind: "file",
      channel: "memory",
      path: "project/f1/profile",
      name: "画像.md",
    });
    expect(opened?.id).toBe(fileTabId("project/f1/profile", null, "memory"));
    expect(opened?.id).not.toBe(fileTabId("project/f1/profile"));

    fireEvent.click(screen.getByRole("button", { name: "删除条目" }));
    expect(useSidePanelStore.getState().tabs).toHaveLength(0);
  });
});
