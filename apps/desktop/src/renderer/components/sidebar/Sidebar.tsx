import { BrandMark } from "@/components/brand/BrandMark";
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

export function Sidebar() {
  const collapsed = useSidebarStore((s) => s.collapsed);
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

  // 「对话」(route "/") 既是「新建对话」动作、又兼作对话区的区段指示：仅在「没有具体会话被
  // 选中」的状态下高亮——空白草稿 `/` 与「全部对话」页 `/conversations`；一旦进入具体会话
  // `/conversations/:id`，高亮就让位给下方最近列表里的那条会话行（避免导航与会话行双重高亮）。
  // 其余导航是普通区段 tab，落在该区段（含子路由）即整段高亮。
  const isNavActive = (route: string) =>
    route === "/"
      ? pathname === "/" || pathname === "/conversations"
      : pathname === route || pathname.startsWith(`${route}/`);

  // 「对话」入口默认就是新建一个空白对话；回到旧对话走下方列表 /「全部对话」。
  const handleNewConversation = () => startNewConversation(navigate);

  const onResizeStart = (e: ReactPointerEvent) => {
    if (collapsed) return;
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
      className={`relative flex flex-shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground ${resizing ? "" : "transition-[width] duration-200"}`}
      style={{
        width: collapsed ? SIDEBAR_COLLAPSED_WIDTH : width,
        backgroundImage: "var(--sidebar-gradient)",
      }}
    >
      {!collapsed && (
        <Button
          variant="ghost"
          aria-label="拖拽调整侧栏宽度（双击还原默认）"
          onPointerDown={onResizeStart}
          onDoubleClick={resetWidth}
          className="absolute right-0 top-0 z-10 h-full w-1 min-w-0 cursor-col-resize rounded-none bg-transparent p-0 hover:bg-primary/40"
        />
      )}

      {/* 浏览器无顶栏：品牌 + 折叠钮放侧栏顶。桌面品牌/折叠仍在 TitleBar。 */}
      {webClient && (
        <div className="px-2 pt-2">
          <div
            className={`flex items-center gap-1 ${collapsed ? "justify-center" : "px-1"}`}
          >
            {!collapsed && (
              <span className="flex flex-1 items-center gap-1.5 text-sidebar-foreground">
                <BrandMark size="sm" />
                {import.meta.env.DEV && (
                  <span className="rounded-full bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground">
                    DEV
                  </span>
                )}
              </span>
            )}
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
          </div>
        </div>
      )}

      {/* 搜索假入口与主导航同一栈（字段感靠浅底 + ⌘K，不是单独成块/分隔线）。 */}
      <nav className="space-y-0.5 px-2 pt-2 pb-2">
        <SearchTrigger collapsed={collapsed} onClick={() => openSearch()} />
        {NAV_ITEMS.map((item) => {
          const active = isNavActive(item.route);
          const showBadge = item.route === "/messages" && unread > 0;
          // 折叠仅图标：补 aria-label + SimpleTooltip，与用户区习惯一致。
          if (collapsed) {
            return (
              <CollapsedNavTip key={item.route} label={item.label}>
                <SurfaceRowButton
                  active={active}
                  aria-label={item.label}
                  onClick={() =>
                    item.route === "/"
                      ? handleNewConversation()
                      : navigate(item.route)
                  }
                  className="relative h-8 justify-center px-0 font-medium"
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
              onClick={() =>
                item.route === "/"
                  ? handleNewConversation()
                  : navigate(item.route)
              }
              // 与下方列表同高（h-8）——整条侧栏一个 34px 节奏；导航的层级由分隔线 +
              // font-medium + 图标承担，不再靠行高撑。
              className="relative h-8 font-medium"
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
            <PinnedConversations />
            <WorkspaceGroups />
            <RecentConversations />
            <ViewAllConversations />
          </RailHotkeySlotsProvider>
        )}
      </div>

      {/* Footer: User menu */}
      <UserMenu />
    </aside>
  );
}
