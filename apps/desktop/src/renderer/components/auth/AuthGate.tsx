import { MinimalTitleBar } from "@/components/layout/TitleBar";
import { isWebClient } from "@/lib/capabilities";
import { logEvent } from "@/lib/log";
import { clearBearerTokens } from "@/lib/sessionAuth";
import { LoginPage } from "@/pages/LoginPage";
import { ServiceUnavailablePage } from "@/pages/ServiceUnavailablePage";
import {
  clearAgentTownSession,
  persistAgentTownSession,
} from "@/services/agentTownSession";
import {
  setServiceUnavailableHandler,
  setSessionRenewedHandler,
  setUnauthorizedHandler,
} from "@/services/api";
import { bootstrapAuth, diagnoseOutage } from "@/services/auth";
import { ensureDefaultContainerRoot } from "@/services/defaultWorkspace";
import {
  cacheShellMeta,
  clearOfflineCache,
  hasOfflineCache,
  hydrateOfflineShell,
} from "@/services/offlineCache";
import { disablePush, enablePush } from "@/services/push";
import { confirmMidSessionOutage } from "@/services/serverHealth";
import { useAuthStore } from "@/stores/auth";
import { useServerHealthStore } from "@/stores/serverHealth";
import { type ReactNode, useCallback, useEffect } from "react";

/** Poll interval while the hard-wall unavailable page is shown (prod + dev). */
export const UNAVAILABLE_BOOTSTRAP_POLL_MS = 5000;

/**
 * Wraps the pre-auth screens (login / loading / 后端不可用) in draggable window chrome.
 * These render outside AppShell — so without this they'd inherit no title bar, leaving a
 * frameless window with no way to move or close it until login succeeds. The web client
 * omits it (the browser provides its own window chrome).
 */
function PreAuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden">
      {!isWebClient() && <MinimalTitleBar />}
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}

/**
 * Gates the whole app behind authentication.
 *
 * On mount it runs {@link bootstrapAuth}, which resolves to authenticated,
 * unauthenticated, or unavailable (backend down), and wires the api-layer 401
 * handler so any later unrecoverable 401 drops straight back to login. Children
 * (the router) only render once authenticated.
 *
 * N4-A 只读离线: mid-session outages stay soft (serverHealth offline banner) when
 * already authenticated; cold-start outage with a local-store cache hydrates into
 * the shell read-only; never-logged-in + no cache still shows the hard wall. That
 * cached shell is an **unverified** session — the server never acknowledged it — so
 * regaining connectivity re-runs this same bootstrap to finish the handshake.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status);
  const reason = useAuthStore((s) => s.reason);

  const enterOfflineReadonly = useCallback(
    async (outageReason: string): Promise<boolean> => {
      if (!(await hasOfflineCache())) return false;
      const user = await hydrateOfflineShell();
      if (!user) return false;
      useAuthStore.getState().setOfflineSession(user);
      useServerHealthStore.getState().markOffline(outageReason, "bootstrap");
      return true;
    },
    [],
  );

  const runBootstrap = useCallback(
    async (opts?: { showLoading?: boolean }) => {
      if (opts?.showLoading !== false) {
        useAuthStore.getState().setLoading();
      }
      try {
        const result = await bootstrapAuth();
        const store = useAuthStore.getState();
        switch (result.kind) {
          case "authenticated":
            store.setAuthenticated(result.user);
            useServerHealthStore.getState().markOnline();
            void persistAgentTownSession();
            void cacheShellMeta({ user: result.user });
            void enablePush();
            break;
          case "unavailable": {
            const entered = await enterOfflineReadonly(result.reason);
            if (!entered) store.setUnavailable(result.reason);
            break;
          }
          case "unauthenticated":
            void clearAgentTownSession();
            store.setUnauthenticated();
            break;
        }
      } catch (err) {
        console.error("[auth] bootstrap failed", err);
        const reason = "连不上 AgentCore 服务，请稍后重试。";
        const entered = await enterOfflineReadonly(reason);
        if (!entered) useAuthStore.getState().setUnavailable(reason);
      }
    },
    [enterOfflineReadonly],
  );

  useEffect(() => {
    // Offline web preview (pnpm dev:web / scripts/shoot.mjs) has no backend; skip
    // auth bootstrap entirely so #/preview renders fully offline.
    if (typeof window !== "undefined" && window.__WEB_PREVIEW__) return;
    setUnauthorizedHandler(() => {
      void disablePush().finally(() => {
        clearBearerTokens();
      });
      void clearAgentTownSession();
      void clearOfflineCache();
      useAuthStore.getState().setUnauthenticated();
    });
    setSessionRenewedHandler(() => void persistAgentTownSession());
    // Mid-session outage (N4-A): stay inside the shell with a soft banner when
    // already authenticated (or already offline-readonly). Confirm via /readyz
    // before markOffline — a healthy probe means ignore the transient API blip
    // (see confirmMidSessionOutage). Never blank the app. Cold-start still uses
    // the hard wall when there is no local-store cache.
    setServiceUnavailableHandler(() => {
      const cur = useAuthStore.getState().status;
      if (cur === "loading" || cur === "unavailable") return;
      if (cur === "authenticated") {
        void confirmMidSessionOutage();
        return;
      }
      void (async () => {
        const reason = await diagnoseOutage();
        if (reason) useAuthStore.getState().setUnavailable(reason);
      })();
    });
    void runBootstrap();
    return () => {
      setUnauthorizedHandler(null);
      setSessionRenewedHandler(null);
      setServiceUnavailableHandler(null);
    };
  }, [runBootstrap]);

  // 认证成功后预热桌面默认本地容器根（遗留本机草稿 / 本地项目创建仍可能用到），
  // 非桌面 / 失败时 no-op，不阻断渲染。
  useEffect(() => {
    if (status === "authenticated") void ensureDefaultContainerRoot();
  }, [status]);

  // Hard-wall「服务不可用」：后端起来后自动恢复，不必只靠手点「重试」。
  // 生产与开发同口径（先前仅 DEV 轮询）。已进壳的离线只读由下面的会话补正接手。
  useEffect(() => {
    if (status !== "unavailable") return;
    const id = window.setInterval(
      () => void runBootstrap({ showLoading: false }),
      UNAVAILABLE_BOOTSTRAP_POLL_MS,
    );
    return () => window.clearInterval(id);
  }, [status, runBootstrap]);

  // 会话补正：离线只读壳是用缓存身份进的，服务端从未确认过这条会话，握手时才下发的东西
  // （首当其冲是 CSRF 令牌）一样都没有——于是读请求全通、写请求全 403，用户看到的是
  // 「点了没反应」。恢复联通只翻连接状态位，会话仍是半成品，所以这里重跑**同一条**权威
  // bootstrap 把握手补完，而不是单独去补某个令牌：bootstrap 打的 `/v1/auth/me` 本身就是
  // 一次握手，响应带回 CSRF 令牌、由 api 层的 captureCsrf 收下，跑完这条会话才真的武装好
  // （否则补正只是刷新了身份，第一条写请求照样 403）。每个 online 边沿最多一次：补正失败
  // 会重新落回离线只读，下一个边沿再试。
  const sessionVerified = useAuthStore((s) => s.sessionVerified);
  const conn = useServerHealthStore((s) => s.status);
  useEffect(() => {
    if (status !== "authenticated" || sessionVerified || conn !== "online") {
      return;
    }
    logEvent("info", "auth.session_reconcile", { trigger: "reconnect" });
    void runBootstrap({ showLoading: false });
  }, [status, sessionVerified, conn, runBootstrap]);

  // Offline web preview: render the app without ever gating on auth.
  if (typeof window !== "undefined" && window.__WEB_PREVIEW__) {
    return <>{children}</>;
  }

  if (status === "loading") {
    return (
      <PreAuthShell>
        <div className="flex h-full w-full items-center justify-center bg-background text-sm text-muted-foreground">
          加载中…
        </div>
      </PreAuthShell>
    );
  }

  if (status === "unavailable") {
    return (
      <PreAuthShell>
        <ServiceUnavailablePage
          reason={reason ?? "连不上 AgentCore 服务，请稍后重试。"}
          onRetry={() => void runBootstrap()}
        />
      </PreAuthShell>
    );
  }

  if (status === "unauthenticated") {
    return (
      <PreAuthShell>
        <LoginPage />
      </PreAuthShell>
    );
  }

  return <>{children}</>;
}
