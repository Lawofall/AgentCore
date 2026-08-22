import { useGroupedConversations } from "@/hooks/useConversations";
import { hasLocalFiles, isWebClient } from "@/lib/capabilities";
import { NarrowLayoutProvider, useNarrowLayoutState } from "@/lib/narrowLayout";
import { GLOBAL_SHORTCUTS, shouldRunGlobalShortcut } from "@/lib/shortcuts";
import { useApplyTheme } from "@/lib/theme";
import { primeDeviceId } from "@/services/deviceIdentity";
import {
  startFulfillStream,
  stopFulfillStream,
} from "@/services/fulfillStream";
import { startRealtime, stopRealtime } from "@/services/realtime";
import { startServerHealthMonitor } from "@/services/serverHealth";
import {
  startNativeNotificationRouting,
  startTeamActivityNotifications,
} from "@/services/teamActivityNotifications";
import { stopAllConversationFollows } from "@/services/turns/conversationFollow";
import { useProductNoticesStore } from "@/stores/productNotices";
import { useSidePanelStore } from "@/stores/sidePanel";
import { useStandingInboxStore } from "@/stores/standingInbox";
import { startUpdates } from "@/stores/updates";
import { useUsageStore } from "@/stores/usage";
import { useEffect, useRef } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { ShareConversationDialog } from "../conversation/ShareConversationDialog";
import { BorrowToCloudDialogHost } from "../files/BorrowToCloudDialog";
import { ConnectGitDialogHost } from "../files/CloneRepoDialog";
import { ImportToCloudDialogHost } from "../files/ImportToCloudDialog";
import { CreateFolderMenuHost } from "../folders/CreateFolderMenu";
import { Sidebar } from "../sidebar/Sidebar";
import { MergeLandingReviewHost } from "../workspace/MergeLandingReview";
import { CommandPalette } from "./CommandPalette";
import { ForceUpdateGate } from "./ForceUpdateGate";
import { NarrowConversationDrawer } from "./NarrowConversationDrawer";
import { NarrowTabBar } from "./NarrowTabBar";
import { NarrowTopBar } from "./NarrowTopBar";
import { OutdatedAndroidBanner } from "./OutdatedAndroidBanner";
import { ProductNoticeBanner } from "./ProductNoticeBanner";
import { ProductNoticeModal } from "./ProductNoticeModal";
import { SidePanelFloatHost } from "./SidePanelFloatHost";
import { TitleBar } from "./TitleBar";
import { UpdateAvailableDialog } from "./UpdateAvailableDialog";
import { WorkspaceChannelBanner } from "./WorkspaceChannelBanner";

export function AppShell() {
  return (
    <NarrowLayoutProvider>
      <AppShellFrame />
    </NarrowLayoutProvider>
  );
}

function AppShellFrame() {
  const { isNarrow } = useNarrowLayoutState();
  const floatsDisabled = isNarrow || window.__NATIVE__ === true;
  useApplyTheme(floatsDisabled);

  useGroupedConversations();

  useEffect(() => {
    void useUsageStore.getState().fetchSummary();
  }, []);

  useEffect(() => {
    primeDeviceId();
    startRealtime();
    startFulfillStream();
    return () => {
      stopRealtime();
      stopFulfillStream();
      stopAllConversationFollows();
    };
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined" && window.__WEB_PREVIEW__) return;
    return useStandingInboxStore.getState().startPolling();
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined" && window.__WEB_PREVIEW__) return;
    return useProductNoticesStore.getState().startPolling();
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined" && window.__WEB_PREVIEW__) return;
    return startServerHealthMonitor();
  }, []);

  useEffect(() => startUpdates(), []);

  useEffect(() => {
    const stopActivity = startTeamActivityNotifications();
    const stopNativeRouting = startNativeNotificationRouting();
    return () => {
      stopActivity();
      stopNativeRouting();
    };
  }, []);

  useEffect(() => {
    if (!floatsDisabled) return;
    useSidePanelStore.getState().clearFloats();
  }, [floatsDisabled]);

  const navigate = useNavigate();
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;

  const { pathname } = useLocation();
  const hideSidebar =
    pathname === "/preview" ||
    pathname.startsWith("/preview/") ||
    pathname.startsWith("/simulation");

  const webClient = isWebClient();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const key = e.key.toLowerCase();
      const match = GLOBAL_SHORTCUTS.find((s) => s.keys.includes(key));
      if (!match) return;
      if (!shouldRunGlobalShortcut(match.id, e.target)) return;
      if (
        isNarrow &&
        (match.id === "toggle-sidebar" ||
          match.id === "open-workspace-terminal")
      ) {
        return;
      }
      if (match.id === "open-workspace-terminal" && !hasLocalFiles()) return;
      e.preventDefault();
      match.run(navigateRef.current);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isNarrow]);

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden">
      {!webClient && !isNarrow && <TitleBar />}
      <WorkspaceChannelBanner />
      <OutdatedAndroidBanner />
      <ProductNoticeBanner />
      <NarrowTopBar />

      <div className="relative flex min-h-0 flex-1 overflow-hidden bg-background">
        {!hideSidebar && !isNarrow && <Sidebar />}
        <main className="relative flex min-h-0 flex-1 overflow-hidden">
          <Outlet />
          {!floatsDisabled && <SidePanelFloatHost />}
        </main>
        <NarrowConversationDrawer />
      </div>
      <NarrowTabBar />

      <ProductNoticeModal />
      <UpdateAvailableDialog />
      <ForceUpdateGate />
      <CommandPalette />
      <ShareConversationDialog />
      <CreateFolderMenuHost />
      <ConnectGitDialogHost />
      <ImportToCloudDialogHost />
      <BorrowToCloudDialogHost />
      <MergeLandingReviewHost />
    </div>
  );
}
