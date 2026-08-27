import { useGroupedConversations } from "@/hooks/useConversations";
import { startAndroidUpdates } from "@/lib/androidUpdates";
import { isWebClient } from "@/lib/capabilities";
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
  // 窄屏 / Capacitor：不上 OS/应用内浮窗（产品页单树；勿从 capabilities 引 isNativeRuntime）。
  const floatsDisabled = isNarrow || window.__NATIVE__ === true;
  // Apply the persisted theme to the DOM and keep it in sync with the OS while
  // set to 跟随系统 (the store only holds the value; this is its sole applier).
  // 窄屏 / Capacitor 仅浅色（前端技术 §五）。
  useApplyTheme(floatsDisabled);

  useEffect(() => {
    if (!floatsDisabled) return;
    useSidePanelStore.getState().clearFloats();
  }, [floatsDisabled]);

  // Warm the grouped query (folders + conversations) at the shell on mount so
  // the sidebar list is ready before it renders — even if the sidebar starts
  // collapsed, the route hasn't mounted a list yet, etc. React Query owns both
  // halves now (folders via useFolders, conversations via useConversations), so
  // there's no store to hydrate here; this call only kicks off the shared fetch.
  useGroupedConversations();

  // Load the account usage summary once on mount so the 用量 dashboard has a
  // warm snapshot before the user opens it. Best-effort: soft error on failure.
  useEffect(() => {
    void useUsageStore.getState().fetchSummary();
  }, []);

  // Open the per-user realtime firehose for the whole authenticated session
  // (消息IM.md §四). It lives at the shell — not the 消息 page — so unread badges
  // and incoming messages update even while the user is elsewhere; it
  // self-manages 401→refresh→reconnect and re-syncs on each (re)connect.
  // Device-level fulfill firehose (`GET /v1/fulfill`) co-lives here: CLIENT_TOOL
  // ops must reach this install even when no conversation SSE is open.
  // Priming the device id here (not only inside the fulfill connect) means the
  // very first turn already carries `X-Client-Device`, so its local ops are
  // pinned to this machine instead of any online install.
  // 对话级订阅（云对话多端同权 B2）挂在会话上、由 hydrate 起停，但它跨路由存活；
  // shell 卸载 = 退出登录 / 关窗，一并硬关，别把一条 SSE 留给下一个账号。
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

  // Standing-task inbox badge (awaiting_user + unacked failed) — soft-poll so
  // Automations → 收件箱 stays live even when the user is elsewhere.
  useEffect(() => {
    if (typeof window !== "undefined" && window.__WEB_PREVIEW__) return;
    return useStandingInboxStore.getState().startPolling();
  }, []);

  // Product notices (全局公告 banner + modal + inbox) — soft-poll; skip offline preview.
  useEffect(() => {
    if (typeof window !== "undefined" && window.__WEB_PREVIEW__) return;
    return useProductNoticesStore.getState().startPolling();
  }, []);

  // Ambient backend-connectivity heartbeat (probes /readyz) so the composer can
  // show whether the server is reachable *before* the user sends — offline preview
  // has no backend, so skip it there. Lives at the shell so it spans the whole
  // authenticated session regardless of route.
  useEffect(() => {
    if (typeof window !== "undefined" && window.__WEB_PREVIEW__) return;
    return startServerHealthMonitor();
  }, []);

  // Auto-update lives at the shell so the consent dialog / "打开安装包" toast (and
  // 关于 page status) stay live regardless of route. Main process schedules checks;
  // download starts only after the user confirms (发布与门禁.md §7.6).
  useEffect(() => startUpdates(), []);
  useEffect(() => startAndroidUpdates(), []);

  // 跨对话完成通知 (前端UX设计.md §一 全局协作感知): ambient, read-only subscription so a team
  // finishing / failing / needing approval in a conversation the user isn't viewing
  // surfaces a toast with a one-click jump. Lives at the shell so it spans every route.
  useEffect(() => {
    const stopActivity = startTeamActivityNotifications();
    const stopNativeRouting = startNativeNotificationRouting();
    return () => {
      stopActivity();
      stopNativeRouting();
    };
  }, []);

  // Global keyboard shortcuts (§二) — dispatched off the single-source table in
  // lib/shortcuts.ts (also rendered by the 快捷键 settings page, so behavior and
  // the documented chord never drift). Chords yield to editable focus (input /
  // textarea / contenteditable) except Cmd/Ctrl+K command palette; navigate is
  // read via a ref so the effect needn't resubscribe on every route change.
  const navigate = useNavigate();
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;

  // The offline preview (#/preview) is a full-window dev surface with its own
  // scenario navigator, so the app's conversation sidebar is pure chrome there.
  // Hide it (the TitleBar stays) to give every replayed AI state — the canvas
  // view especially — the full window width.
  const { pathname } = useLocation();
  const hideSidebar =
    pathname === "/preview" || pathname.startsWith("/preview/");

  // 生产 web 客户端不画桌面窗口顶栏（浏览器自带窗口 chrome）——品牌/折叠改由侧栏顶部
  // 承载（见 Sidebar）。搜索假入口两端都在侧栏。桌面 Electron 外壳与离线预览 #/preview 仍保留顶栏。
  const webClient = isWebClient();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const key = e.key.toLowerCase();
      const match = GLOBAL_SHORTCUTS.find((s) => s.keys.includes(key));
      if (!match) return;
      if (!shouldRunGlobalShortcut(match.id, e.target)) return;
      e.preventDefault();
      match.run(navigateRef.current);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
          {/* 浮窗壳：宽屏桌面常挂（UX §十，⊥ SidePanel.open）；窄屏 / Capacitor 不挂。 */}
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
