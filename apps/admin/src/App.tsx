import { AdminShell } from "@/components/AdminShell";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { AccountPage } from "@/pages/AccountPage";
import { AnalyticsPage } from "@/pages/AnalyticsPage";
import { AuditPage } from "@/pages/AuditPage";
import { ConversationsPage } from "@/pages/ConversationsPage";
import { ForcePasswordChangePage } from "@/pages/ForcePasswordChangePage";
import { LoginPage } from "@/pages/LoginPage";
import { MfaSetupPage } from "@/pages/MfaSetupPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { OverviewPage } from "@/pages/OverviewPage";
import { PreviewPage } from "@/pages/PreviewPage";
import { ReplayPage } from "@/pages/ReplayPage";
import { BetaGroupPage } from "@/pages/BetaGroupPage";
import { NoticesPage } from "@/pages/NoticesPage";
import { PlatformQuotaPage } from "@/pages/PlatformQuotaPage";
import { SkillStorePage } from "@/pages/SkillStorePage";
import { SystemPage } from "@/pages/SystemPage";
import { UsersPage } from "@/pages/UsersPage";
import { setUnauthorizedHandler } from "@/services/api";
import { logout } from "@/services/auth";
import { bootstrap } from "@/services/session";
import { useAuthStore } from "@/stores/auth";
import { ShieldAlert } from "lucide-react";
import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

export function App() {
  const status = useAuthStore((s) => s.status);
  const passwordMustChange = useAuthStore(
    (s) => s.user?.passwordMustChange ?? false,
  );
  const mfaSetupRequired = useAuthStore((s) => s.mfaSetupRequired);

  useEffect(() => {
    setUnauthorizedHandler(() => useAuthStore.getState().setUnauthenticated());
    void bootstrap();
    return () => setUnauthorizedHandler(null);
  }, []);

  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center text-muted-foreground">
        <Spinner className="size-5" />
      </div>
    );
  }

  if (status === "unavailable") {
    return (
      <CenteredCard
        title="无法连接后端"
        description="请确认后端服务已启动后重试。"
      >
        <Button onClick={() => void bootstrap()}>重试</Button>
      </CenteredCard>
    );
  }

  if (status === "unauthenticated") {
    return <LoginPage />;
  }

  if (status === "forbidden") {
    return (
      <CenteredCard
        icon={<ShieldAlert size={24} className="text-warning" />}
        title="需要管理员权限"
        description="当前账号不是平台管理员，无法访问管理后台。"
      >
        <Button
          variant="outline"
          onClick={() =>
            void logout().finally(() =>
              useAuthStore.getState().setUnauthenticated(),
            )
          }
        >
          退出登录
        </Button>
      </CenteredCard>
    );
  }

  if (passwordMustChange) {
    return <ForcePasswordChangePage />;
  }

  if (mfaSetupRequired) {
    return <MfaSetupPage />;
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AdminShell />}>
          <Route index element={<Navigate to="/overview" replace />} />
          <Route path="overview" element={<OverviewPage />} />
          <Route path="users" element={<UsersPage />} />
          <Route path="users/:userId" element={<UsersPage />} />
          <Route path="analytics" element={<Navigate to="/analytics/cost" replace />} />
          <Route path="analytics/:segment" element={<AnalyticsPage />} />
          <Route path="conversations" element={<Navigate to="/conversations/conversations" replace />} />
          <Route path="conversations/:segment" element={<ConversationsPage />} />
          <Route path="replay/:conversationId" element={<ReplayPage />} />
          <Route path="preview" element={<PreviewPage />} />
          <Route path="audit" element={<AuditPage />} />
          <Route path="notices" element={<NoticesPage />} />
          <Route path="store" element={<SkillStorePage />} />
          <Route path="beta-group" element={<BetaGroupPage />} />
          <Route path="quota" element={<PlatformQuotaPage />} />
          <Route path="system" element={<SystemPage />} />
          <Route path="account" element={<AccountPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

function CenteredCard({
  icon,
  title,
  description,
  children,
}: {
  icon?: React.ReactNode;
  title: string;
  description: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="flex w-full max-w-xs flex-col items-center gap-4 text-center">
        {icon && (
          <div className="flex size-12 items-center justify-center rounded-xl bg-warning/10">
            {icon}
          </div>
        )}
        <div>
          <h1 className="text-xl font-semibold text-foreground">{title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        </div>
        {children}
      </div>
    </div>
  );
}
