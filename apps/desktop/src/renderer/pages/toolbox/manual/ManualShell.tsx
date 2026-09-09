import { WindowControls } from "@/components/layout/WindowControls";
import { Button, SearchField, SurfaceRowButton } from "@/components/ui";
import { isMac, macTitleBarInsetClass } from "@/lib/platform";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/stores/ui";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  List,
  type LucideIcon,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { CONTENT_CHAPTERS } from "./content";
import { resolveManualIcon } from "./icons";
import { APP_PATHS } from "./paths";
import {
  type SearchEntry,
  buildContentSearchEntries,
  matchSnippet,
} from "./searchIndex";

export interface ManualNavItem {
  id: string;
  label: string;
  Icon: LucideIcon;
}

export interface ManualChapter {
  id: string;
  path: string;
  label: string;
  items: ManualNavItem[];
}

/** 侧栏目录直接从内容源派生——标签/图标与正文单源，不再手工维护。 */
export const CHAPTERS: ManualChapter[] = CONTENT_CHAPTERS.map((chapter) => ({
  id: chapter.id,
  path: chapter.path,
  label: chapter.label,
  items: chapter.sections.map((section) => ({
    id: section.id,
    label: section.title,
    Icon: resolveManualIcon(section.icon),
  })),
}));

/** 搜索索引：全部章节由内容源生成（标题 + 正文 + FAQ 问句全文）。 */
const SEARCH_ENTRIES: SearchEntry[] = buildContentSearchEntries();

function getAdjacentChapters(currentPath: string) {
  const idx = CHAPTERS.findIndex((c) => currentPath.startsWith(c.path));
  return {
    prev: idx > 0 ? CHAPTERS[idx - 1] : null,
    next: idx < CHAPTERS.length - 1 ? CHAPTERS[idx + 1] : null,
  };
}

function ManualNavBody({
  query,
  onQueryChange,
  results,
  currentChapter,
  activeId,
  onNavClick,
  onResultClick,
  onChapterClick,
}: {
  query: string;
  onQueryChange: (v: string) => void;
  results: SearchEntry[];
  currentChapter: ManualChapter | undefined;
  activeId: string | null;
  onNavClick: (chapter: ManualChapter, itemId: string) => void;
  onResultClick: (entry: SearchEntry) => void;
  onChapterClick: (path: string) => void;
}) {
  const q = query.trim();
  return (
    <>
      <div className="shrink-0 p-3">
        <SearchField
          value={query}
          onValueChange={onQueryChange}
          placeholder="搜索手册…"
          aria-label="搜索手册"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-6">
        {q ? (
          <div className="space-y-0.5">
            {results.length === 0 ? (
              <p className="px-3 py-2 text-xs text-muted-foreground">
                没找到相关内容
              </p>
            ) : (
              results.map((r) => {
                const Icon = r.Icon ?? resolveManualIcon(r.icon ?? "BookOpen");
                const snippet = r.body ? matchSnippet(r.body, query) : r.group;
                return (
                  <SurfaceRowButton
                    key={r.id}
                    variant="settings"
                    onClick={() => onResultClick(r)}
                    className="h-auto gap-2.5 px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent/60 hover:text-foreground"
                  >
                    <Icon size={16} className="shrink-0" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{r.label}</span>
                      <span className="block truncate text-xs text-muted-foreground/70">
                        {snippet}
                      </span>
                    </span>
                  </SurfaceRowButton>
                );
              })
            )}
          </div>
        ) : (
          <div className="space-y-5">
            {CHAPTERS.map((chapter) => {
              const isCurrent = currentChapter?.id === chapter.id;
              return (
                <div key={chapter.id}>
                  <button
                    type="button"
                    onClick={() => onChapterClick(chapter.path)}
                    className={cn(
                      "w-full px-3 pb-1.5 text-left text-xs font-medium transition-colors",
                      isCurrent
                        ? "text-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {chapter.label}
                  </button>
                  <div className="space-y-0.5">
                    {chapter.items.map((item) => {
                      const Icon = item.Icon;
                      const isActive = isCurrent && activeId === item.id;
                      return (
                        <SurfaceRowButton
                          key={item.id}
                          variant="settings"
                          onClick={() => onNavClick(chapter, item.id)}
                          className={cn(
                            "h-9 gap-2.5 px-3 text-sm hover:bg-accent/60 hover:text-foreground",
                            isActive
                              ? "bg-accent text-foreground"
                              : "text-muted-foreground",
                          )}
                        >
                          <Icon size={16} className="shrink-0" />
                          <span className="truncate">{item.label}</span>
                        </SurfaceRowButton>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

export function ManualShell() {
  const navigate = useNavigate();
  const location = useLocation();
  const exit = useCallback(() => navigate(APP_PATHS.more.about), [navigate]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const currentChapter = CHAPTERS.find((c) =>
    location.pathname.startsWith(c.path),
  );

  const results = useMemo(() => {
    if (!q) return [];
    return SEARCH_ENTRIES.filter(
      (e) => e.haystack.includes(q) || e.group.toLowerCase().includes(q),
    );
  }, [q]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || useUIStore.getState().searchOpen) return;
      if (drawerOpen) {
        setDrawerOpen(false);
        return;
      }
      exit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [exit, drawerOpen]);

  // 切章时关抽屉
  // biome-ignore lint/correctness/useExhaustiveDependencies: pathname 变化即关
  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  // 滚动高亮（scroll-spy）：观察当前章节各小节标题，高亮目录中对应项。
  // biome-ignore lint/correctness/useExhaustiveDependencies: `location.pathname` resets scroll-spy when switching manual chapters.
  useEffect(() => {
    setActiveId(currentChapter?.items[0]?.id ?? null);
    if (!currentChapter) return;
    const root = scrollRef.current;
    if (!root) return;
    let obs: IntersectionObserver | null = null;
    // 等子页 section 挂载后再观察（Outlet 在本帧后渲染）。
    const raf = requestAnimationFrame(() => {
      const els = currentChapter.items
        .map((it) => document.getElementById(it.id))
        .filter((el): el is HTMLElement => el != null);
      if (els.length === 0) return;
      obs = new IntersectionObserver(
        (entries) => {
          const visible = entries
            .filter((e) => e.isIntersecting)
            .sort(
              (a, b) => a.boundingClientRect.top - b.boundingClientRect.top,
            );
          if (visible[0]) setActiveId(visible[0].target.id);
        },
        { root, rootMargin: "0px 0px -70% 0px", threshold: 0 },
      );
      for (const el of els) obs.observe(el);
    });
    return () => {
      cancelAnimationFrame(raf);
      obs?.disconnect();
    };
  }, [currentChapter, location.pathname]);

  const handleNavClick = (chapter: ManualChapter, itemId: string) => {
    setDrawerOpen(false);
    if (location.pathname !== chapter.path) {
      navigate(`${chapter.path}?s=${itemId}`);
    } else {
      document
        .getElementById(itemId)
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
      setActiveId(itemId);
    }
  };

  const handleResultClick = (entry: SearchEntry) => {
    setDrawerOpen(false);
    navigate(entry.to);
    setQuery("");
  };

  const { prev, next } = getAdjacentChapters(location.pathname);

  const navProps = {
    query,
    onQueryChange: setQuery,
    results,
    currentChapter,
    activeId,
    onNavClick: handleNavClick,
    onResultClick: handleResultClick,
    onChapterClick: (path: string) => {
      setDrawerOpen(false);
      navigate(path);
    },
  };

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background">
      {/* Header */}
      <header
        className={`flex h-12 shrink-0 items-center border-b border-border [-webkit-app-region:drag] ${isMac ? macTitleBarInsetClass : ""}`}
      >
        <Button
          variant="neutral"
          size="md"
          onClick={exit}
          icon={<ArrowLeft size={16} />}
          className="ml-2 [-webkit-app-region:no-drag]"
        >
          返回
        </Button>
        <Button
          variant="ghost"
          size="md"
          onClick={() => setDrawerOpen(true)}
          icon={<List size={16} />}
          className="ml-1 [-webkit-app-region:no-drag] md:hidden"
          aria-label="打开目录"
        />
        <span className="ml-1 text-sm font-medium text-foreground">
          产品手册
        </span>
        {currentChapter && (
          <>
            <ChevronRight
              size={14}
              className="ml-1.5 text-muted-foreground/60"
            />
            <span className="ml-1.5 truncate text-sm text-muted-foreground">
              {currentChapter.label}
            </span>
          </>
        )}
        <div className="flex-1" />
        <WindowControls
          className="flex items-center [-webkit-app-region:no-drag]"
          buttonClassName="size-12 rounded-none"
        />
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Left sidebar：常驻完整目录 + 搜索（≥md） */}
        <nav className="hidden w-[260px] shrink-0 flex-col overflow-hidden border-r border-border bg-muted/30 md:flex">
          <ManualNavBody {...navProps} />
        </nav>

        {/* 窄屏目录抽屉 */}
        {drawerOpen && (
          <div className="fixed inset-0 z-50 md:hidden">
            <button
              type="button"
              className="absolute inset-0 bg-background/60"
              aria-label="关闭目录"
              onClick={() => setDrawerOpen(false)}
            />
            <nav className="absolute inset-y-0 left-0 flex w-[min(300px,85vw)] flex-col overflow-hidden border-r border-border bg-background shadow-lg">
              <div className="flex h-12 shrink-0 items-center justify-between border-b border-border px-3">
                <span className="text-sm font-medium text-foreground">
                  目录
                </span>
                <Button
                  variant="ghost"
                  size="md"
                  onClick={() => setDrawerOpen(false)}
                  icon={<X size={16} />}
                  aria-label="关闭"
                />
              </div>
              <ManualNavBody {...navProps} />
            </nav>
          </div>
        )}

        {/* Content area */}
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />

          {/* Prev / Next navigation */}
          {(prev || next) && (
            <div className="mx-auto w-full max-w-3xl px-6 pb-10">
              <div className="flex items-center justify-between border-t border-border pt-6">
                {prev ? (
                  <Button
                    variant="neutral"
                    size="md"
                    onClick={() => navigate(prev.path)}
                    icon={<ChevronLeft size={16} />}
                  >
                    {prev.label}
                  </Button>
                ) : (
                  <div />
                )}
                {next ? (
                  <Button
                    variant="neutral"
                    size="md"
                    onClick={() => navigate(next.path)}
                    className="flex-row-reverse"
                    icon={<ChevronRight size={16} />}
                  >
                    {next.label}
                  </Button>
                ) : (
                  <div />
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
