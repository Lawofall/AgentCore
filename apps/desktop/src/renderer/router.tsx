import { ConversationRoute } from "@/components/chat/ConversationRoute";
import { AppShell } from "@/components/layout/AppShell";
import { RouteError } from "@/components/layout/RouteError";
import { NarrowBlockedPage } from "@/lib/narrowLayout";
import { AskCommencePreviewPage } from "@/pages/AskCommencePreviewPage";
import { ConversationsPage } from "@/pages/ConversationsPage";
import { ConversationsPreviewPage } from "@/pages/ConversationsPreviewPage";
import { FilesPage } from "@/pages/FilesPage";
import { FilesPreviewPage } from "@/pages/FilesPreviewPage";
import { FloatWindowPage } from "@/pages/FloatWindowPage";
import { MessagesPage } from "@/pages/MessagesPage";
import { MorePage } from "@/pages/MorePage";
import { OnboardingPreviewPage } from "@/pages/OnboardingPreviewPage";
import { PreviewPage } from "@/pages/PreviewPage";
import { TurnDetailPage } from "@/pages/TurnDetailPage";
import { WhiteboardCanvasPage } from "@/pages/WhiteboardCanvasPage";
import { WhiteboardPage } from "@/pages/WhiteboardPage";
import { WhiteboardPreviewPage } from "@/pages/WhiteboardPreviewPage";
import { LegalSettingsPage } from "@/pages/legal/LegalSettingsPage";
import { AboutSettings } from "@/pages/more/AboutSettings";
import { AccountSettings } from "@/pages/more/AccountSettings";
import { GeneralSettings } from "@/pages/more/GeneralSettings";
import { GitCredentialSettings } from "@/pages/more/GitCredentialSettings";
import { ImPrivacySettings } from "@/pages/more/ImPrivacySettings";
import { ModelSettings } from "@/pages/more/ModelSettings";
import { MoreIndexRedirect } from "@/pages/more/MoreIndexRedirect";
import { ProviderSettings } from "@/pages/more/ProviderSettings";
import { RedirectToOfficialChat } from "@/pages/more/RedirectToOfficialChat";
import { ShortcutsSettings } from "@/pages/more/ShortcutsSettings";
import { UsageSettings } from "@/pages/more/UsageSettings";
import { CreationPage } from "@/pages/toolbox/CreationPage";
import { GuidelinesPage } from "@/pages/toolbox/GuidelinesPage";
import { ToolboxShell } from "@/pages/toolbox/ToolboxShell";
import { ToolsPage } from "@/pages/toolbox/ToolsPage";
import {
  ManualCollaboration,
  ManualIntro,
  ManualMechanism,
  ManualReference,
  ManualShell,
} from "@/pages/toolbox/manual";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { MarketPage } from "@/pages/toolbox/market/MarketPage";
import { WorkflowEditorPage, WorkflowsPage } from "@/pages/toolbox/workflows";
import { Navigate, createHashRouter } from "react-router-dom";

export const router = createHashRouter([
  // Desktop OS float window (UX §十 · 方案 C): sibling of AppShell so it skips
  // sidebar / main dock / app TitleBar. Hash: #/float?cid=…&tab=….
  {
    path: "/float",
    element: <FloatWindowPage />,
    errorElement: <RouteError />,
  },
  {
    path: "/",
    element: <AppShell />,
    // Catches both an unmatched path (404) and any error thrown while rendering a
    // child route, so the user lands on an app-styled page instead of React
    // Router's bare default. Errors bubble to this nearest boundary.
    errorElement: <RouteError />,
    children: [
      {
        element: <ConversationRoute />,
        children: [{ index: true }, { path: "conversations/:id" }],
      },
      {
        path: "conversations/:id/turn/:turnId",
        element: <TurnDetailPage />,
      },
      {
        path: "conversations",
        element: (
          <NarrowBlockedPage>
            <ConversationsPage />
          </NarrowBlockedPage>
        ),
      },
      { path: "files", element: <FilesPage /> },
      {
        path: "whiteboard",
        element: (
          <NarrowBlockedPage>
            <WhiteboardPage />
          </NarrowBlockedPage>
        ),
      },
      {
        path: "whiteboard/:boardId",
        element: (
          <NarrowBlockedPage>
            <WhiteboardCanvasPage />
          </NarrowBlockedPage>
        ),
      },
      { path: "messages", element: <MessagesPage /> },
      { path: "messages/:chatId", element: <MessagesPage /> },
      // Official product_notice in-app detail (官方号双模板 · 图文/长文).
      {
        path: "messages/:chatId/notices/:noticeId",
        element: <MessagesPage />,
      },
      {
        path: "toolbox",
        element: (
          <NarrowBlockedPage>
            <ToolboxShell />
          </NarrowBlockedPage>
        ),
        children: [
          { index: true, element: <Navigate to="mine/skills" replace /> },
          { path: "mine/skills", element: <GuidelinesPage /> },
          { path: "mine/tools", element: <ToolsPage /> },
          { path: "mine/creation", element: <CreationPage /> },
          {
            path: "mine/mcp",
            element: <Navigate to={APP_PATHS.toolbox.connectors} replace />,
          },
          {
            path: "mine/automations",
            element: <Navigate to={APP_PATHS.toolbox.workflows.root} replace />,
          },
          { path: "mine/workflows", element: <WorkflowsPage /> },
          { path: "market", element: <MarketPage /> },
        ],
      },
      {
        path: "toolbox/tools",
        element: <Navigate to={APP_PATHS.toolbox.mine.tools} replace />,
      },
      {
        path: "toolbox/guidelines",
        element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
      },
      {
        path: "toolbox/store",
        element: <Navigate to={APP_PATHS.toolbox.market} replace />,
      },
      {
        path: "toolbox/connectors",
        element: <Navigate to={APP_PATHS.toolbox.connectors} replace />,
      },
      {
        path: "toolbox/automations",
        element: <Navigate to={APP_PATHS.toolbox.workflows.root} replace />,
      },
      {
        path: "toolbox/automations/inbox",
        element: <Navigate to={APP_PATHS.toolbox.workflows.root} replace />,
      },
      {
        path: "toolbox/workflows",
        element: <Navigate to={APP_PATHS.toolbox.workflows.root} replace />,
      },
      {
        path: "toolbox/workflows/:workflowId",
        element: (
          <NarrowBlockedPage>
            <WorkflowEditorPage />
          </NarrowBlockedPage>
        ),
      },
      {
        path: "toolbox/manual",
        element: (
          <NarrowBlockedPage>
            <ManualShell />
          </NarrowBlockedPage>
        ),
        children: [
          { index: true, element: <Navigate to="intro" replace /> },
          { path: "intro", element: <ManualIntro /> },
          { path: "collaboration", element: <ManualCollaboration /> },
          { path: "mechanism", element: <ManualMechanism /> },
          { path: "reference", element: <ManualReference /> },
        ],
      },
      // Day2 公共市场未落地：旧书签 #/explore 收向工具箱，避免「即将上线」空壳。
      {
        path: "explore",
        element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
      },
      // 旧自动化 / 收件箱书签全部收向工作流列表。
      {
        path: "more/automations",
        element: <Navigate to={APP_PATHS.toolbox.workflows.root} replace />,
      },
      {
        path: "more/inbox",
        element: <Navigate to={APP_PATHS.toolbox.workflows.root} replace />,
      },
      // 产品公告 inbox 已迁 IM 官方号；旧书签 / 手册路径收向消息页。
      { path: "more/notices", element: <RedirectToOfficialChat /> },
      // Hidden dev route — not in the nav; reach it by typing #/preview. Replays
      // committed conformance vectors through the real dispatch to eyeball every AI
      // state offline (no backend / LLM). See preview/replay.ts.
      { path: "preview", element: <PreviewPage /> },
      // Companion offline preview for the self-built whiteboard canvas (a scene surface, not an
      // SSE vector — see preview/whiteboardScenes.ts + scripts/shoot-whiteboard.mjs).
      { path: "preview/whiteboard", element: <WhiteboardPreviewPage /> },
      // Preview：已退役 ask 开场布局对照（现生产 = 通用澄清卡）。
      { path: "preview/ask-commence", element: <AskCommencePreviewPage /> },
      // Preview 首启体验（草稿空态两态 + composer 生成中插话态）.
      { path: "preview/onboarding", element: <OnboardingPreviewPage /> },
      // Preview 全部对话管理页（时间线列表 · mock 数据离线自检）.
      { path: "preview/conversations", element: <ConversationsPreviewPage /> },
      // Preview 文件页 AgentCore 扁平条目轨（常驻用量 · 徽章 · description）.
      { path: "preview/files", element: <FilesPreviewPage /> },
      {
        path: "more",
        element: <MorePage />,
        children: [
          // Opening 设置：platform / 已有平台或服务商 → 模型；byok 空接 → 服务商。
          { index: true, element: <MoreIndexRedirect /> },
          { path: "model", element: <ModelSettings /> },
          { path: "providers", element: <ProviderSettings /> },
          {
            path: "git",
            element: (
              <NarrowBlockedPage>
                <GitCredentialSettings />
              </NarrowBlockedPage>
            ),
          },
          { path: "account", element: <AccountSettings /> },
          { path: "messages", element: <ImPrivacySettings /> },
          { path: "usage", element: <UsageSettings /> },
          {
            path: "general",
            element: (
              <NarrowBlockedPage>
                <GeneralSettings />
              </NarrowBlockedPage>
            ),
          },
          // 「外观」已改名「通用」并收编了原关于页的诊断类开关；旧路径仍是既有
          // 书签与外部深链的目标，故留重定向。
          {
            path: "appearance",
            element: (
              <NarrowBlockedPage>
                <Navigate to="/more/general" replace />
              </NarrowBlockedPage>
            ),
          },
          {
            path: "shortcuts",
            element: (
              <NarrowBlockedPage>
                <ShortcutsSettings />
              </NarrowBlockedPage>
            ),
          },
          // 「反馈」工单页已撤；旧书签收向关于（联系说明在该页）。不要
          // NarrowBlocked：窄屏也要落到关于，而不是设置列表。
          {
            path: "feedback",
            element: <Navigate to={APP_PATHS.more.about} replace />,
          },
          { path: "about", element: <AboutSettings /> },
          { path: "legal/:docId", element: <LegalSettingsPage /> },
        ],
      },
    ],
  },
]);
