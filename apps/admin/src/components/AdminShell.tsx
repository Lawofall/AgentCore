import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import { logout } from "@/services/auth";
import { useAuthStore } from "@/stores/auth";
import {
  BarChart3,
  LayoutDashboard,
  LogOut,
  type LucideIcon,
  Megaphone,
  MessageSquare,
  Menu,
  ScrollText,
  Server,
  ShieldCheck,
  Users,
  UsersRound,
  Wallet,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
// Plain `Link`, not `NavLink`: NavLink derives `aria-current` from its own path match,
// which would drop the highlight on sub-routes that have no nav entry of their own
// (/replay/:id belongs to 对话, /users/:id to 用户). `isNavActive` owns that decision.
import { Link, Outlet, useLocation } from "react-router-dom";
import { toast } from "sonner";

/**
 * The console's sections: 概览 / 用户 / 对话 / 分析 / 审计 / 公告 / 内测群 / 平台额度 / 系统.
 * URL-routed via react-router for bookmarkable deep links.
 */
export type AdminTab =
  | "overview"
  | "users"
  | "conversations"
  | "analytics"
  | "audit"
  | "notices"
  | "beta-group"
  | "quota"
  | "system";

interface NavItem {
  id: AdminTab;
  label: string;
  icon: LucideIcon;
  path: string;
  /** Sub-routes that should keep this item lit (e.g. /users/:id, /replay/:id). */
  match?: string[];
}

/**
 * Nine flat entries read as one undifferentiated list. Grouping them by what the
 * operator is doing — watching the platform, investigating a case, administering it —
 * gives the sidebar a shape you can scan instead of read.
 */
const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "监控",
    items: [
      { id: "overview", label: "概览", icon: LayoutDashboard, path: "/overview" },
      { id: "analytics", label: "分析", icon: BarChart3, path: "/analytics/cost", match: ["/analytics"] },
    ],
  },
  {
    label: "排查",
    items: [
      {
        id: "conversations",
        label: "对话",
        icon: MessageSquare,
        path: "/conversations/conversations",
        match: ["/conversations", "/replay"],
      },
      { id: "audit", label: "审计", icon: ScrollText, path: "/audit" },
    ],
  },
  {
    label: "管理",
    items: [
      { id: "users", label: "用户", icon: Users, path: "/users", match: ["/users"] },
      { id: "notices", label: "公告", icon: Megaphone, path: "/notices" },
      { id: "beta-group", label: "内测群", icon: UsersRound, path: "/beta-group" },
      { id: "quota", label: "平台额度", icon: Wallet, path: "/quota" },
      { id: "system", label: "系统", icon: Server, path: "/system" },
    ],
  },
];

function isNavActive(item: NavItem, pathname: string): boolean {
  if (item.match) return item.match.some((p) => pathname.startsWith(p));
  return pathname === item.path;
}

const DRAWER_ID = "admin-nav-drawer";
const FOCUSABLE =
  'a[href],button:not([disabled]),[tabindex]:not([tabindex="-1"])';

export function AdminShell() {
  const location = useLocation();
  const user = useAuthStore((s) => s.user);
  const setUnauthenticated = useAuthStore((s) => s.setUnauthenticated);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const drawerRef = useRef<HTMLElement>(null);

  // A new page should start at the top, and the drawer should not stay open over it.
  useEffect(() => {
    setDrawerOpen(false);
    if (mainRef.current) mainRef.current.scrollTop = 0;
  }, [location.pathname, location.search]);

  // Move into the drawer on open, hand focus back to the trigger on close.
  useEffect(() => {
    if (!drawerOpen) return;
    const opener = document.activeElement as HTMLElement | null;
    const panel = drawerRef.current;
    (panel?.querySelector<HTMLElement>(FOCUSABLE) ?? panel)?.focus();
    return () => opener?.focus?.();
  }, [drawerOpen]);

  // Past `lg` the drawer and its scrim are display:none while `drawerOpen` is still
  // true — which would leave the column below marked inert with nothing on screen
  // explaining why it stopped responding.
  useEffect(() => {
    if (!drawerOpen) return;
    // 1024px is Tailwind's `lg`, the width at which the sidebar docks.
    const closeIfDocked = () => {
      if (window.innerWidth >= 1024) setDrawerOpen(false);
    };
    window.addEventListener("resize", closeIfDocked);
    return () => window.removeEventListener("resize", closeIfDocked);
  }, [drawerOpen]);

  // Esc dismisses, Tab cycles inside: the drawer claims `aria-modal`, so keyboard
  // focus has to honour that instead of walking off into the covered page.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setDrawerOpen(false);
        return;
      }
      if (e.key !== "Tab") return;
      const nodes = drawerRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!nodes || nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [drawerOpen]);

  const handleLogout = async () => {
    try {
      await logout();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setUnauthenticated();
    }
  };

  const displayName = user?.displayName || user?.username;
  const accountActive = location.pathname.startsWith("/account");

  /** `onClose` is passed by the drawer copy only — the docked sidebar has nothing to close. */
  const renderSidebar = (onClose?: () => void) => (
    <>
      <div className="flex h-14 shrink-0 items-center gap-2.5 px-5 font-semibold text-foreground text-sm">
        <ShieldCheck size={18} className="text-primary" />
        管理后台
        {onClose && (
          <Button
            variant="ghost"
            size="sm"
            aria-label="关闭导航"
            className="-mr-2 ml-auto px-2"
            onClick={onClose}
          >
            <X size={18} />
          </Button>
        )}
      </div>
      <nav aria-label="主导航" className="flex-1 overflow-y-auto px-3 pb-3">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mb-4 last:mb-0">
            {/* Full-strength muted, no alpha: at this size a 70% wash of it lands
                around 2.7:1, and the group label is the one thing on screen that
                repeats on every page. */}
            <p className="px-3 pb-1.5 text-xs font-medium tracking-wide text-muted-foreground">
              {group.label}
            </p>
            <div className="flex flex-col gap-0.5">
              {group.items.map((item) => {
                const Icon = item.icon;
                const active = isNavActive(item, location.pathname);
                return (
                  <Link
                    key={item.id}
                    to={item.path}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex h-9 items-center gap-2.5 rounded-lg px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                      active
                        ? "bg-accent font-medium text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                    )}
                  >
                    <Icon size={16} className="shrink-0" />
                    {item.label}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="flex shrink-0 items-center gap-2 border-border border-t p-3">
        <Link
          to="/account"
          aria-current={accountActive ? "page" : undefined}
          className={cn(
            "flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2 py-1.5 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
            accountActive ? "bg-accent" : "hover:bg-accent/60",
          )}
          title={displayName}
        >
          <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 font-medium text-primary text-xs uppercase">
            {(displayName ?? "?").charAt(0)}
          </span>
          <span
            className={cn(
              "min-w-0 flex-1 truncate text-sm",
              accountActive ? "text-foreground" : "text-muted-foreground",
            )}
          >
            {displayName}
          </span>
        </Link>
        <Button
          variant="ghost"
          size="sm"
          aria-label="退出登录"
          className="shrink-0 px-2 text-muted-foreground hover:text-foreground"
          onClick={() => void handleLogout()}
        >
          <LogOut size={16} />
        </Button>
      </div>
    </>
  );

  return (
    <div className="flex h-dvh overflow-hidden bg-background">
      <a
        href="#main"
        className="sr-only rounded-lg bg-card px-3 py-2 text-sm focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:ring-2 focus:ring-ring"
      >
        跳到主内容
      </a>

      <aside className="hidden w-56 shrink-0 flex-col border-border border-r bg-muted/30 lg:flex">
        {renderSidebar()}
      </aside>

      {drawerOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          {/* Mouse affordance only, hidden from assistive tech — the same call the
              Dialog scrim makes. The panel's 关闭导航 button and Esc are the paths
              that have to work, and a second control by that name would announce
              twice. biome-ignore lint/a11y/noStaticElementInteractions: see above */}
          <div
            aria-hidden="true"
            className="absolute inset-0 bg-overlay"
            onMouseDown={() => setDrawerOpen(false)}
          />
          <aside
            ref={drawerRef}
            id={DRAWER_ID}
            role="dialog"
            aria-modal="true"
            aria-label="导航"
            tabIndex={-1}
            className="relative flex h-full w-64 flex-col border-border border-r bg-card outline-none"
          >
            {renderSidebar(() => setDrawerOpen(false))}
          </aside>
        </div>
      )}

      {/* Inert while the drawer is open: the panel and its scrim cover this column
          whole — including the trigger below — so leaving it reachable is what let
          a "打开导航" button sit there claiming aria-expanded="true". */}
      <div className="flex min-w-0 flex-1 flex-col" inert={drawerOpen}>
        <header className="flex h-14 shrink-0 items-center gap-3 border-border border-b px-4 lg:hidden">
          <Button
            variant="ghost"
            size="sm"
            aria-label="打开导航"
            aria-haspopup="dialog"
            aria-expanded={drawerOpen}
            aria-controls={drawerOpen ? DRAWER_ID : undefined}
            className="px-2"
            onClick={() => setDrawerOpen(true)}
          >
            <Menu size={18} />
          </Button>
          <span className="flex items-center gap-2 font-semibold text-foreground text-sm">
            <ShieldCheck size={16} className="text-primary" />
            管理后台
          </span>
        </header>

        <main
          id="main"
          ref={mainRef}
          tabIndex={-1}
          className={cn(
            "flex-1 outline-none",
            location.pathname.startsWith("/replay/")
              ? "flex min-h-0 flex-col overflow-hidden"
              : "overflow-y-auto",
          )}
        >
          <ErrorBoundary resetKey={location.pathname}>
            {location.pathname.startsWith("/replay/") ? (
              <div className="flex h-full min-h-0 flex-col">
                <Outlet />
              </div>
            ) : (
              <Outlet />
            )}
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
