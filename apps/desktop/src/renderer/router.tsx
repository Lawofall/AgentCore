import { ConversationRoute } from "@/components/chat/ConversationRoute";
import { AppShell } from "@/components/layout/AppShell";
import { RouteError } from "@/components/layout/RouteError";
import { NarrowBlockedPage } from "@/lib/narrowLayout";
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
import { LegalSettingsPage } from "@/pages/legal/LegalSettingsPage";
import { AboutSettings } from "@/pages/more/AboutSettings";
import { AccountSettings } from "@/pages/more/AccountSettings";
import { GeneralSettings } from "@/pages/more/GeneralSettings";
import { GitCredentialSettings } from "@/pages/more/GitCredentialSettings";
import { ImPrivacySettings } from "@/pages/more/ImPrivacySettings";
import { ModelSettingsRoute } from "@/pages/more/ModelSettings";
import { MoreIndexRedirect } from "@/pages/more/MoreIndexRedirect";
import { ProviderSettings } from "@/pages/more/ProviderSettings";
import { RedirectToOfficialChat } from "@/pages/more/RedirectToOfficialChat";
import { ShortcutsSettings } from "@/pages/more/ShortcutsSettings";
import { SponsorSettings } from "@/pages/more/SponsorSettings";
import { UsageSettings } from "@/pages/more/UsageSettings";
import { AssemblyShelfPage } from "@/pages/toolbox/AssemblyShelf";
import { FactoryGuidePage } from "@/pages/toolbox/FactoryGuidePage";
import { ToolboxShell } from "@/pages/toolbox/ToolboxShell";
import {
  AssemblySectionRedirect,
  OfficialShelfRedirect,
} from "@/pages/toolbox/assemblyPages";
import {
  ManualCollaboration,
  ManualIntro,
  ManualMechanism,
  ManualReference,
  ManualShell,
} from "@/pages/toolbox/manual";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { MarketPage } from "@/pages/toolbox/market/MarketPage";
import { Navigate, createHashRouter } from "react-router-dom";

export const router = createHashRouter([
  // Desktop OS float window (UX §四 · 方案 C): sibling of AppShell so it skips
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
        element: <ConversationsPage />,
      },
      { path: "files", element: <FilesPage /> },
      {
        path: "whiteboard",
        element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
      },
      {
        path: "whiteboard/:boardId",
        element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
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
          { index: true, element: <AssemblyShelfPage /> },
          {
            path: "overview",
            element: <AssemblySectionRedirect tab="overview" />,
          },
          { path: "model", element: <AssemblySectionRedirect tab="model" /> },
          { path: "tools", element: <AssemblySectionRedirect tab="tools" /> },
          {
            path: "envelope",
            element: <AssemblySectionRedirect tab="envelope" />,
          },
          { path: "official", element: <OfficialShelfRedirect /> },
          {
            path: "mine/skills",
            element: <AssemblySectionRedirect tab="prompts" />,
          },
          { path: "mcp", element: <AssemblySectionRedirect tab="plugs" /> },
          {
            path: "mine/tools",
            element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
          },
          {
            path: "mine/creation",
            element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
          },
          {
            path: "mine/automations",
            element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
          },
          {
            path: "mine/workflows",
            element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
          },
        ],
      },
      {
        path: "toolbox/market",
        element: (
          <NarrowBlockedPage>
            <MarketPage />
          </NarrowBlockedPage>
        ),
      },
      {
        path: "toolbox/guides",
        element: (
          <NarrowBlockedPage>
            <FactoryGuidePage />
          </NarrowBlockedPage>
        ),
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
        path: "toolbox/automations",
        element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
      },
      {
        path: "toolbox/automations/inbox",
        element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
      },
      {
        path: "toolbox/workflows",
        element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
      },
      {
        path: "toolbox/workflows/:workflowId",
        element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
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
      // 旧书签 #/explore 收向提示词；市场在 #/toolbox/market。
      {
        path: "explore",
        element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
      },
      // 旧工作流 / 自动化书签进提示词。
      {
        path: "more/automations",
        element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
      },
      {
        path: "more/inbox",
        element: <Navigate to={APP_PATHS.toolbox.mine.skills} replace />,
      },
      // 产品公告 inbox 已迁 IM 官方号；旧书签 / 手册路径收向消息页。
      { path: "more/notices", element: <RedirectToOfficialChat /> },
      // Hidden dev route — not in the nav; reach it by typing #/preview. Replays
      // committed conformance vectors through the real dispatch to eyeball every AI
      // state offline (no backend / LLM). See preview/replay.ts.
      { path: "preview", element: <PreviewPage /> },
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
          { index: true, element: <MoreIndexRedirect /> },
          {
            path: "model",
            element: <ModelSettingsRoute />,
            children: [
              { index: true, element: <AssemblyShelfPage /> },
              {
                path: "overview",
                element: <AssemblySectionRedirect tab="overview" />,
              },
              {
                path: "model",
                element: <AssemblySectionRedirect tab="model" />,
              },
              {
                path: "tools",
                element: <AssemblySectionRedirect tab="tools" />,
              },
              {
                path: "envelope",
                element: <AssemblySectionRedirect tab="envelope" />,
              },
              {
                path: "prompts",
                element: <AssemblySectionRedirect tab="prompts" />,
              },
              {
                path: "plugs",
                element: <AssemblySectionRedirect tab="plugs" />,
              },
            ],
          },
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
          { path: "sponsor", element: <SponsorSettings /> },
          { path: "legal/:docId", element: <LegalSettingsPage /> },
        ],
      },
    ],
  },
]);
