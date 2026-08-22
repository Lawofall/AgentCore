// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({
  useGroupedConversations: () => undefined,
  useConversations: () => [],
  useConversationTrash: () => ({
    data: { items: [], retentionDays: 30 },
    isLoading: false,
  }),
  useRestoreConversation: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/useFolders", () => ({
  useFolders: () => [],
}));
vi.mock("@/lib/theme", () => ({
  useApplyTheme: () => undefined,
}));
vi.mock("@/lib/capabilities", () => ({
  isWebClient: () => true,
  hasLocalFiles: () => false,
  isNativeRuntime: () => false,
}));
vi.mock("@/services/realtime", () => ({
  startRealtime: vi.fn(),
  stopRealtime: vi.fn(),
}));
vi.mock("@/services/fulfillStream", () => ({
  startFulfillStream: vi.fn(),
  stopFulfillStream: vi.fn(),
}));
vi.mock("@/services/serverHealth", () => ({
  startServerHealthMonitor: () => () => undefined,
}));
vi.mock("@/services/teamActivityNotifications", () => ({
  startTeamActivityNotifications: () => () => undefined,
  startNativeNotificationRouting: () => () => undefined,
}));
vi.mock("@/stores/productNotices", () => ({
  useProductNoticesStore: {
    getState: () => ({ startPolling: () => () => undefined }),
  },
}));
vi.mock("@/stores/standingInbox", () => ({
  useStandingInboxStore: {
    getState: () => ({ startPolling: () => () => undefined }),
  },
}));
vi.mock("@/stores/updates", () => ({
  startUpdates: () => () => undefined,
}));
vi.mock("@/stores/usage", () => ({
  useUsageStore: {
    getState: () => ({ fetchSummary: () => Promise.resolve() }),
  },
}));
vi.mock("@/components/sidebar/Sidebar", () => ({
  Sidebar: () => null,
}));
vi.mock("@/components/layout/TitleBar", () => ({
  TitleBar: () => null,
}));
vi.mock("@/components/layout/CommandPalette", () => ({
  CommandPalette: () => null,
}));
vi.mock("@/components/layout/ForceUpdateGate", () => ({
  ForceUpdateGate: () => null,
}));
vi.mock("@/components/layout/ProductNoticeBanner", () => ({
  ProductNoticeBanner: () => null,
}));
vi.mock("@/components/layout/ProductNoticeModal", () => ({
  ProductNoticeModal: () => null,
}));
vi.mock("@/components/layout/UpdateAvailableDialog", () => ({
  UpdateAvailableDialog: () => null,
}));
vi.mock("@/components/layout/WorkspaceChannelBanner", () => ({
  WorkspaceChannelBanner: () => null,
}));
vi.mock("@/components/conversation/ShareConversationDialog", () => ({
  ShareConversationDialog: () => null,
}));
vi.mock("@/components/folders/CreateFolderMenu", () => ({
  CreateFolderMenuHost: () => null,
}));
vi.mock("@/components/files/CloneRepoDialog", () => ({
  ConnectGitDialogHost: () => null,
  CloneRepoDialog: () => null,
}));
vi.mock("@/components/files/ImportToCloudDialog", () => ({
  ImportToCloudDialogHost: () => null,
  ImportToCloudDialog: () => null,
}));
vi.mock("@/components/files/BorrowToCloudDialog", () => ({
  BorrowToCloudDialogHost: () => null,
  BorrowToCloudDialog: () => null,
}));
vi.mock("@/components/workspace/MergeLandingReview", () => ({
  MergeLandingReviewHost: () => null,
}));
vi.mock("@/components/layout/SidePanelFloatHost", () => ({
  SidePanelFloatHost: () => <div data-testid="side-panel-float-host" />,
}));

import { AppShell } from "@/components/layout/AppShell";
import { WORKSPACE_TAB_ID, useSidePanelStore } from "@/stores/sidePanel";

function stubMatchMedia(matches: boolean): void {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

function renderMore(): void {
  render(
    <MemoryRouter initialEntries={["/more"]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/more" element={<div>more</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function seedWorkspaceFloat(): void {
  useSidePanelStore.setState({
    floats: [
      {
        tabId: WORKSPACE_TAB_ID,
        layout: { x: 72, y: 72, width: 480, height: 560, zIndex: 1 },
      },
    ],
    focusSurface: { type: "float", tabId: WORKSPACE_TAB_ID },
  });
}

afterEach(() => {
  cleanup();
  window.__NATIVE__ = undefined;
  useSidePanelStore.setState({
    floats: [],
    focusSurface: { type: "dock" },
  });
});

describe("AppShell SidePanelFloatHost mount", () => {
  it("keeps the float host mounted on non-conversation routes", () => {
    stubMatchMedia(false);
    renderMore();
    expect(screen.getByTestId("side-panel-float-host")).toBeTruthy();
    expect(screen.getByText("more")).toBeTruthy();
  });

  it("does not mount the float host on a narrow viewport", () => {
    stubMatchMedia(true);
    seedWorkspaceFloat();
    renderMore();
    expect(screen.queryByTestId("side-panel-float-host")).toBeNull();
    expect(useSidePanelStore.getState().floats).toHaveLength(0);
  });

  it("does not mount the float host when window.__NATIVE__ is set", () => {
    stubMatchMedia(false);
    window.__NATIVE__ = true;
    seedWorkspaceFloat();
    renderMore();
    expect(screen.queryByTestId("side-panel-float-host")).toBeNull();
    expect(useSidePanelStore.getState().floats).toHaveLength(0);
  });
});
