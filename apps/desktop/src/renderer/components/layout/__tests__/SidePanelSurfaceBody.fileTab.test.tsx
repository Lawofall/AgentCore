// @vitest-environment jsdom

import { SidePanelSurfaceBody } from "@/components/layout/SidePanelSurfaceBody";
import type { FileSource } from "@/lib/fileSource";
import { type DetailTab, useSidePanelStore } from "@/stores/sidePanel";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { useFileTabSourceState } = vi.hoisted(() => ({
  useFileTabSourceState: vi.fn(),
}));

vi.mock("@/hooks/useConversationFileSource", () => ({ useFileTabSourceState }));
vi.mock("@/components/files/FileDetail", () => ({
  FileDetail: ({ source }: { source: FileSource }) => (
    <div data-testid="file-detail" data-source={source.id} />
  ),
}));
vi.mock("@/components/chat/ApprovalPrompt", () => ({
  ApprovalPrompt: () => null,
}));
vi.mock("@/components/chat/Markdown", () => ({ Markdown: () => null }));
vi.mock("@/components/chat/detail/RunDetailScroll", () => ({
  RunDetailScroll: () => null,
}));
vi.mock("@/components/workspace/ConversationChangesPanel", () => ({
  ConversationChangesPanel: () => null,
}));
vi.mock("@/components/workspace/WorkspacePanel", () => ({
  WorkspaceMode: () => null,
}));

const fileTab: DetailTab = {
  id: "file:1",
  kind: "file",
  title: "gap.md",
  path: "AgentCore/文档/research/gap.md",
  name: "gap.md",
  workspaceId: "folder:local-proj",
};

beforeEach(() => {
  vi.clearAllMocks();
  useSidePanelStore.setState({ tabs: [fileTab], activeTabId: fileTab.id });
});

afterEach(cleanup);

/**
 * 浮窗冷启（新渲染进程、名册未落地）时，文件体必须等归属定下来再挂
 * {@link FileDetail}——抢跑会拿错源发读请求，用户看到 `API 404: 文件不存在`。
 */
describe("SidePanelSurfaceBody — 文件 tab 的未知期", () => {
  it("挂起时显示定位中，不挂 FileDetail（即不发读请求）", () => {
    useFileTabSourceState.mockReturnValue({ source: null, pending: true });
    render(<SidePanelSurfaceBody tabId={fileTab.id} />);
    expect(screen.getByText("正在定位文件…")).toBeTruthy();
    expect(screen.queryByTestId("file-detail")).toBeNull();
  });

  it("确认无源时才说没有文件源", () => {
    useFileTabSourceState.mockReturnValue({ source: null, pending: false });
    render(<SidePanelSurfaceBody tabId={fileTab.id} />);
    expect(screen.getByText("当前会话尚无可用文件源。")).toBeTruthy();
  });

  it("源到位后渲染文件详情", () => {
    useFileTabSourceState.mockReturnValue({
      source: { id: "local:root-1" } as FileSource,
      pending: false,
    });
    render(<SidePanelSurfaceBody tabId={fileTab.id} />);
    expect(screen.getByTestId("file-detail").getAttribute("data-source")).toBe(
      "local:root-1",
    );
  });

  it("memory 通道用设定源，不走工作区盘", () => {
    useFileTabSourceState.mockReturnValue({
      source: { id: "workspace:cloud" } as FileSource,
      pending: false,
    });
    const memoryTab: DetailTab = {
      id: "file:memory:project/f1/profile",
      kind: "file",
      title: "画像.md",
      path: "project/f1/profile",
      name: "画像.md",
      channel: "memory",
    };
    useSidePanelStore.setState({
      tabs: [memoryTab],
      activeTabId: memoryTab.id,
    });
    render(<SidePanelSurfaceBody tabId={memoryTab.id} />);
    expect(screen.getByTestId("file-detail").getAttribute("data-source")).toBe(
      "memory",
    );
  });
});
