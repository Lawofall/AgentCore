import { BrandMarkIcon } from "@/components/brand/BrandMark";
import {
  Button,
  IconButton,
  SearchTrigger,
  SurfaceRowButton,
} from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { isWebClient } from "@/lib/capabilities";
import { startNewConversation } from "@/lib/newConversation";
import { RailHotkeySlotsProvider } from "@/lib/railHotkeys";
import { cn } from "@/lib/utils";
import { useUnreadTotal } from "@/stores/messaging";
import { SIDEBAR_COLLAPSED_WIDTH, useSidebarStore } from "@/stores/sidebar";
import { useUIStore } from "@/stores/ui";
import {
  Files,
  Mail,
  MessageSquare,
  PanelLeft,
  PanelLeftClose,
  Wrench,
  X,
} from "lucide-react";
import type { ReactNode, PointerEvent as ReactPointerEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { PinnedConversations } from "./PinnedConversations";
import {
  RecentConversations,
  ViewAllConversations,
} from "./RecentConversations";
import { UserMenu } from "./UserMenu";
import { WorkspaceGroups } from "./WorkspaceGroups";

const NAV_ITEMS = [
  { icon: MessageSquare, label: "新对话", route: "/" },
  { icon: Files, label: "文件", route: "/files" },
  { icon: Mail, label: "消息", route: "/messages" },
  { icon: Wrench, label: "工具箱", route: "/toolbox" },
] as const;

/** 折叠侧栏图标按钮：右侧 tip，与 UserMenu 习惯一致。 */
function CollapsedNavTip({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <SimpleTooltip label={label} side="right">
      {children}
    </SimpleTooltip>
  );
}

export function Sidebar({
  overlay = false,
  onDismiss,
}: {
  overlay?: boolean;
  onDismiss?: () => void;
} = {}) {
  const storeCollapsed = useSidebarStore((s) => s.collapsed);
  const collapsed = overlay ? false : storeCollapsed;
  const width = useSidebarStore((s) => s.width);
  const resizing = useSidebarStore((s) => s.resizing);
  const setWidth = useSidebarStore((s) => s.setWidth);
  const setResizing = useSidebarStore((s) => s.setResizing);
  const resetWidth = useSidebarStore((s) => s.resetWidth);
  const toggleCollapsed = useSidebarStore((s) => s.toggleCollapsed);
  const openSearch = useUIStore((s) => s.openSearch);
  const unread = useUnreadTotal();
  const navigate = useNavigate();
  const { pathname } = useLocation();

  // 浏览器版没有桌面顶栏（AppShell 已隐藏），品牌 / 折叠按钮改由侧栏顶部承载。
  // 搜索假入口两端都在侧栏（桌面顶栏不再放）。桌面 & 离线预览仍用顶栏放品牌/折叠。
  const webClient = isWebClient();
  const navItems = overlay
    ? NAV_ITEMS.filter((item) => item.route !== "/toolbox")
    : NAV_ITEMS;

  // 顶栏区段跟路由；对话行选中态也跟路由（`conversationLocationId`），避免非对话页
  // 与上场会话双高亮。`/` 兼「新建」与对话区段指示：仅空白草稿与「全部对话」页高亮，
  // 进入 `/conversations/:id` 后让位给列表里那一行。其余导航落在该区段（含子路由）即亮。
  const isNavActive = (route: string) =>
    route === "/"
      ? pathname === "/" || pathname === "/conversations"
      : pathname === route || pathname.startsWith(`${route}/`);

  const goNav = (route: string) => {
    if (route === "/") startNewConversation(navigate);
    else navigate(route);
    onDismiss?.();
  };

  const onResizeStart = (e: ReactPointerEvent) => {
    if (collapsed || overlay) return;
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = width;
    setResizing(true);
    const onMove = (ev: PointerEvent) =>
      setWidth(startWidth + (ev.clientX - startX));
    const onUp = () => {
      setResizing(false);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  return (
    <aside
      className={cn(
        "relative flex flex-shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground",
        overlay
          ? "absolute inset-y-0 left-0 z-40 w-[min(20rem,86vw)] shadow-md"
          : resizing
            ? ""
            : "transition-[width] duration-200",
        overlay &&
          "pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]",
      )}
      style={
        overlay
          ? { backgroundImage: "var(--sidebar-gradient)" }
          : {
              width: collapsed ? SIDEBAR_COLLAPSED_WIDTH : width,
              backgroundImage: "var(--sidebar-gradient)",
            }
      }
      role={overlay ? "dialog" : undefined}
      aria-label={overlay ? "侧栏" : undefined}
    >
      {!collapsed && !overlay && (
        <Button
          variant="ghost"
          aria-label="拖拽调整侧栏宽度（双击还原默认）"
          onPointerDown={onResizeStart}
          onDoubleClick={resetWidth}
          className="absolute right-0 top-0 z-10 h-full w-1 min-w-0 cursor-col-resize rounded-none bg-transparent p-0 hover:bg-primary/40"
        />
      )}

      {/* overlay：无窗口顶栏，品牌 + 关闭。web 宽屏：品牌 + 折叠。Electron 宽屏品牌/折叠在 TitleBar。 */}
      {(overlay || webClient) && (
        <div className="px-2 pt-2">
          <div
            className={`flex items-center gap-1 ${collapsed ? "justify-center" : "px-1"}`}
          >
            {!collapsed && (
              <span className="flex flex-1 items-center gap-1.5 text-sidebar-foreground">
                <BrandMarkIcon size={16} title="AgentCore" />
              </span>
            )}
            {overlay ? (
              <IconButton
                tone="sidebar"
                onClick={() => onDismiss?.()}
                aria-label="关闭侧栏"
              >
                <X size={16} />
              </IconButton>
            ) : (
              <IconButton
                tone="sidebar"
                onClick={toggleCollapsed}
                aria-label={collapsed ? "展开侧栏" : "折叠侧栏"}
              >
                {collapsed ? (
                  <PanelLeft size={16} />
                ) : (
                  <PanelLeftClose size={16} />
                )}
              </IconButton>
            )}
          </div>
        </div>
      )}

      {/* 搜索假入口与主导航同一栈（字段感靠浅底 + ⌘K，不是单独成块/分隔线）。 */}
      <nav className="space-y-0.5 px-2 pt-2 pb-2">
        <SearchTrigger
          collapsed={collapsed}
          onClick={() => {
            openSearch();
            onDismiss?.();
          }}
        />
        {navItems.map((item) => {
          const active = isNavActive(item.route);
          const showBadge = item.route === "/messages" && unread > 0;
          // 折叠仅图标：补 aria-label + SimpleTooltip，与用户区习惯一致。
          if (collapsed) {
            return (
              <CollapsedNavTip key={item.route} label={item.label}>
                <SurfaceRowButton
                  active={active}
                  aria-label={item.label}
                  onClick={() => goNav(item.route)}
                  className="touch-row relative h-8 justify-center px-0 font-medium"
                >
                  <item.icon size={16} className="shrink-0" />
                  {showBadge && (
                    <span
                      aria-label={`${unread} 条未读`}
                      className="absolute right-2 top-1.5 size-2 rounded-full bg-primary"
                    />
                  )}
                </SurfaceRowButton>
              </CollapsedNavTip>
            );
          }
          return (
            <SurfaceRowButton
              key={item.route}
              active={active}
              onClick={() => goNav(item.route)}
              // 宽屏与对话行同高（h-8）。窄屏 / 粗指针用 touch-row 抬到 44px，
              // 与对话行同一热区；层级仍靠分隔线 + font-medium + 图标。
              className="touch-row relative h-8 font-medium"
            >
              <item.icon size={16} className="shrink-0" />
              <span>{item.label}</span>
              {showBadge && (
                <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1 text-xs font-medium text-primary-foreground">
                  {unread > 99 ? "99+" : unread}
                </span>
              )}
            </SurfaceRowButton>
          );
        })}
      </nav>

      {/* Divider — nav vs conversation list */}
      <div className="mx-3 border-t border-sidebar-border" />

      {/* 置顶 (全局) → 文件夹 → 快速对话 (未置顶裸聊); full list on /conversations
          (前端UX §一 方案C). */}
      <div className="flex-1 overflow-y-auto">
        {!collapsed && (
          <RailHotkeySlotsProvider>
            <PinnedConversations onActivate={onDismiss} />
            <WorkspaceGroups onActivate={onDismiss} />
            <RecentConversations onActivate={onDismiss} />
            <ViewAllConversations onActivate={onDismiss} />
          </RailHotkeySlotsProvider>
        )}
      </div>

      {/* Footer: User menu */}
      <UserMenu />
    </aside>
  );
}
