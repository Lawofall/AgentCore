import {
  ChevronRight,
  Compass,
  Mail,
  MessageSquare,
  Minus,
  MoreVertical,
  PanelLeftClose,
  Plus,
  Search,
  Square,
  Wrench,
  X,
} from "lucide-react";
import type { ReactNode } from "react";
import { useVideoConfig } from "remotion";

/*
 * The desktop shell, re-stated (apps/video/README.md): a static, pixel-faithful
 * copy of AppShell + TitleBar + Sidebar markup with the store/IPC/router wiring
 * stripped out. It's the 全程基线 — present behind every "app" scene — so the
 * chrome reads as one continuous running app while only the main area changes.
 *
 * Layout mirrors AppShell exactly: TitleBar (h-10) over a row of Sidebar (w-60)
 * + main. MAIN_W / MAIN_H helpers derive the content box from the composition
 * size (via useVideoConfig), which scenes use to size the graph / chat column.
 */

export const SIDEBAR_W = 240;
export const TITLEBAR_H = 40;

/** Content box inside the shell for a composition of `width`×`height`. */
export function mainBox(
  width: number,
  height: number,
): { width: number; height: number } {
  return { width: width - SIDEBAR_W, height: height - TITLEBAR_H };
}

export type PromoTheme = "light" | "dark";

export interface RecentConversation {
  title: string;
  active: boolean;
  running: boolean;
}

const NAV_ITEMS = [
  { icon: MessageSquare, label: "对话", active: true },
  { icon: Mail, label: "消息", active: false },
  { icon: Wrench, label: "工具箱", active: false },
  { icon: Compass, label: "探索", active: false },
] as const;

function TitleBar() {
  return (
    <header className="flex h-10 shrink-0 items-center border-b border-sidebar-border bg-sidebar">
      <div className="flex w-60 items-center gap-2 px-3">
        <span className="flex-1 text-sm font-semibold text-sidebar-foreground">
          AgentCore
        </span>
        <span className="flex size-7 items-center justify-center rounded-lg text-sidebar-foreground/60">
          <PanelLeftClose size={16} />
        </span>
      </div>
      <div className="flex-1" />
      <div className="flex items-center">
        <span className="mr-2 flex h-7 items-center gap-2 rounded-lg border border-sidebar-border px-3 text-sm text-sidebar-foreground/60">
          <Search size={13} className="shrink-0" />
          <span>搜索…</span>
          <kbd className="text-xs text-sidebar-foreground/40">Ctrl+K</kbd>
        </span>
        <span className="flex h-10 w-12 items-center justify-center text-sidebar-foreground/60">
          <Minus size={14} />
        </span>
        <span className="flex h-10 w-12 items-center justify-center text-sidebar-foreground/60">
          <Square size={12} />
        </span>
        <span className="flex h-10 w-12 items-center justify-center text-sidebar-foreground/60">
          <X size={14} />
        </span>
      </div>
    </header>
  );
}

function Sidebar({ recent }: { recent: RecentConversation[] }) {
  return (
    <aside
      className="flex w-60 flex-shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground"
      style={{ backgroundImage: "var(--sidebar-gradient)" }}
    >
      <nav className="space-y-0.5 px-2 pt-2 pb-2">
        {NAV_ITEMS.map((item) => (
          <div
            key={item.label}
            className={`relative flex h-9 w-full items-center gap-3 rounded-lg px-3 text-base ${
              item.active
                ? "bg-sidebar-accent text-sidebar-accent-foreground"
                : "text-sidebar-foreground"
            }`}
          >
            <item.icon size={18} className="shrink-0" />
            <span className="flex-1 text-left">{item.label}</span>
          </div>
        ))}
      </nav>

      <div className="mx-3 border-t border-sidebar-border" />
      <div className="flex items-center justify-between px-4 pt-3 pb-1">
        <span className="text-xs font-medium text-sidebar-foreground/50">
          对话
        </span>
        <span className="flex size-7 items-center justify-center rounded-lg text-sidebar-foreground/50">
          <Plus size={14} />
        </span>
      </div>

      <div className="flex-1 overflow-hidden">
        <div className="space-y-0.5 px-2 py-1">
          {recent.map((c) => (
            <div
              key={c.title}
              className={`flex h-9 w-full items-center gap-2 rounded-lg px-3 text-sm text-sidebar-list ${
                c.active ? "bg-sidebar-accent" : ""
              }`}
            >
              {c.running && (
                <span className="size-1.5 shrink-0 rounded-full bg-primary" />
              )}
              <span className="flex-1 truncate text-left">{c.title}</span>
            </div>
          ))}
          <div className="mt-1 flex h-9 w-full items-center justify-between gap-2 rounded-lg px-3 text-sm text-sidebar-foreground/55">
            <span>查看全部对话</span>
            <ChevronRight size={14} className="shrink-0" />
          </div>
        </div>
      </div>

      <div className="border-t border-sidebar-border p-2">
        <div className="flex items-center gap-3 px-3">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-sidebar-accent text-sm font-medium text-sidebar-accent-foreground">
            我
          </div>
          <span className="flex-1 truncate text-sm text-sidebar-foreground">
            我的工作台
          </span>
          <span className="flex size-7 items-center justify-center rounded-lg text-sidebar-foreground/50">
            <MoreVertical size={14} />
          </span>
        </div>
      </div>
    </aside>
  );
}

/** The full desktop shell with `children` rendered into the main content area. */
export function PromoShell({
  children,
  recent,
  theme = "light",
}: {
  children: ReactNode;
  recent: RecentConversation[];
  theme?: PromoTheme;
}) {
  const { width, height } = useVideoConfig();
  return (
    <div
      className={`flex flex-col overflow-hidden bg-background${theme === "dark" ? " dark" : ""}`}
      style={{ width, height }}
    >
      <TitleBar />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <Sidebar recent={recent} />
        <main className="relative flex min-h-0 flex-1 overflow-hidden">
          {children}
        </main>
      </div>
    </div>
  );
}
