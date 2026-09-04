// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
vi.mock("@/hooks/useFolderSharing", () => ({
  useSharedWithMeFolders: () => ({ data: [] }),
  usePendingFolderInvites: () => ({ data: [] }),
}));
vi.mock("@/lib/theme", () => ({
  useApplyTheme: () => undefined,
}));
vi.mock("@/lib/capabilities", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/capabilities")>();
  return {
    ...actual,
    isWebClient: () => true,
  };
});
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
  Sidebar: () => <div data-testid="desktop-sidebar" />,
}));
vi.mock("@/components/layout/TitleBar", () => ({
  TitleBar: () => <div data-testid="desktop-titlebar" />,
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
}));
vi.mock("@/components/files/ImportToCloudDialog", () => ({
  ImportToCloudDialogHost: () => null,
}));
vi.mock("@/components/files/BorrowToCloudDialog", () => ({
  BorrowToCloudDialogHost: () => null,
}));
vi.mock("@/components/workspace/MergeLandingReview", () => ({
  MergeLandingReviewHost: () => null,
}));
vi.mock("@/components/layout/SidePanelFloatHost", () => ({
  SidePanelFloatHost: () => null,
}));

import { AppShell } from "@/components/layout/AppShell";

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

afterEach(() => {
  cleanup();
});

describe("AppShell narrow chrome", () => {
  beforeEach(() => {
    stubMatchMedia(true);
  });

  it("shows 4-tab bar and hides the desktop sidebar on a conversation route", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<div>chat</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByLabelText("主导航")).toBeTruthy();
    expect(screen.getByText("对话")).toBeTruthy();
    expect(screen.getByText("消息")).toBeTruthy();
    expect(screen.getByText("文件")).toBeTruthy();
    expect(screen.getByText("我的")).toBeTruthy();
    expect(screen.queryByTestId("desktop-sidebar")).toBeNull();
    expect(screen.getByLabelText("对话列表")).toBeTruthy();
  });

  it("hides the tab bar on an IM thread", () => {
    render(
      <MemoryRouter initialEntries={["/messages/c1"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/messages/:chatId" element={<div>thread</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.queryByLabelText("主导航")).toBeNull();
  });
});
