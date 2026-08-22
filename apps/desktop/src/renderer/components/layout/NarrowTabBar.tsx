import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { isNarrowChatRoute } from "@/lib/useNarrowLayout";
import { cn } from "@/lib/utils";
import { useUnreadTotal } from "@/stores/messaging";
import { Files, Mail, MessageSquare, User } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";

const TABS = [
  { label: "对话", route: "/", Icon: MessageSquare },
  { label: "消息", route: "/messages", Icon: Mail },
  { label: "文件", route: "/files", Icon: Files },
  { label: "我的", route: "/more", Icon: User },
] as const;

let lastChatPath = "/";

function rememberChatPath(pathname: string): void {
  if (isNarrowChatRoute(pathname) && !pathname.includes("/turn/")) {
    lastChatPath = pathname === "/conversations" ? "/" : pathname;
  }
}

function isTabActive(pathname: string, route: string): boolean {
  if (route === "/") return isNarrowChatRoute(pathname);
  return pathname === route || pathname.startsWith(`${route}/`);
}

function chatTabTarget(pathname: string): string {
  if (isTabActive(pathname, "/")) return "/";
  return lastChatPath;
}

export function NarrowTabBar() {
  const { isNarrow, hideChrome } = useNarrowLayoutState();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const unread = useUnreadTotal();

  rememberChatPath(pathname);

  if (!isNarrow || hideChrome) return null;

  return (
    <nav
      className="flex shrink-0 border-t border-border bg-card pb-[env(safe-area-inset-bottom)]"
      aria-label="主导航"
    >
      {TABS.map(({ label, route, Icon }) => {
        const active = isTabActive(pathname, route);
        const showBadge = route === "/messages" && unread > 0;
        return (
          <button
            key={route}
            type="button"
            onClick={() =>
              navigate(route === "/" ? chatTabTarget(pathname) : route)
            }
            className={cn(
              "relative flex min-h-12 flex-1 flex-col items-center justify-center gap-0.5 text-xs",
              active ? "text-primary" : "text-muted-foreground",
            )}
            aria-current={active ? "page" : undefined}
          >
            <Icon size={20} />
            {label}
            {showBadge && (
              <span className="absolute right-[calc(50%-22px)] top-1.5 size-1.5 rounded-full bg-primary" />
            )}
          </button>
        );
      })}
    </nav>
  );
}
