import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { getConversations } from "@/hooks/useConversations";
import { getFolders, useFolders } from "@/hooks/useFolders";
import { isNativeRuntime } from "@/lib/capabilities";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import {
  COMMAND_CATEGORY_ORDER,
  type PaletteCommand,
  buildPaletteCommands,
  commandMatches,
} from "@/lib/paletteCommands";
import { queryClient } from "@/lib/queryClient";
import { conversationKeys } from "@/lib/queryKeys";
import {
  TIME_FILTER_LABELS,
  TIME_FILTER_ORDER,
  type TimeFilter,
  timeFilterSince,
} from "@/lib/searchFilters";
import { filesFocusState } from "@/pages/conversations/constants";
import { folderHitHasNoLiveChat } from "@/pages/conversations/liveFolderCounts";
import { fetchDemoTapeCatalog } from "@/services/demoTape";
import { dedupeFoldersByLocalBinding } from "@/services/folders";
import { jumpToMessage } from "@/services/messages";
import {
  type SearchItem,
  type SearchSectionType,
  searchAll,
} from "@/services/search";
import { useConversationStore } from "@/stores/conversation";
import { useSidebarStore } from "@/stores/sidebar";
import { useUIStore } from "@/stores/ui";
import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import {
  Check,
  ChevronDown,
  Folder,
  Loader2,
  MessageSquare,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

/** Per-section cap, and the recent-conversations count for the empty query. */
const PER_TYPE_LIMIT = 8;
/** Wait this long after the last keystroke before hitting the backend. */
const DEBOUNCE_MS = 300;

/** Shared row styling (selected state driven by cmdk). */
const ROW_CLASS =
  "flex cursor-pointer items-center gap-3 px-4 py-2 text-foreground transition-colors data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground";

const GROUP_CLASS =
  "[&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground/70";

/** A selectable row: a Tier 2 command (local) or an entity search hit (backend). */
type Row =
  | { kind: "command"; cmd: PaletteCommand }
  | { kind: "entity"; type: SearchSectionType; item: SearchItem };

/** A rendered group: a heading and its rows (commands by category, or one
 * entity type — reused for the empty-query "recent conversations" list). */
interface RenderGroup {
  key: string;
  heading: string;
  rows: Row[];
}

const SECTION_LABEL: Record<SearchSectionType, string> = {
  conversation: "对话",
  message: "消息",
  folder: "文件夹",
};

const SECTION_ICON: Record<SearchSectionType, typeof MessageSquare> = {
  conversation: MessageSquare,
  message: MessageSquare,
  folder: Folder,
};

/** Slice a snippet around its match offsets for highlighting; falls back to the
 * plain text when the offsets are missing or out of range. */
function Snippet({ item }: { item: SearchItem }) {
  const text = item.snippet ?? "";
  const start = item.match_start;
  const end = item.match_end;
  if (
    start == null ||
    end == null ||
    start < 0 ||
    end > text.length ||
    start >= end
  ) {
    return <span className="truncate text-muted-foreground">{text}</span>;
  }
  return (
    <span className="truncate text-muted-foreground">
      {text.slice(0, start)}
      <mark className="bg-primary/20 text-foreground">
        {text.slice(start, end)}
      </mark>
      {text.slice(end)}
    </span>
  );
}

/**
 * Palette filter bar: time/workspace chips while a query is present.
 * Not mounted on an empty query so the chip row does not render empty.
 */
function PaletteFilterBar({
  timeFilter,
  onTimeFilter,
  folders,
  folderId,
  onFolderId,
}: {
  timeFilter: TimeFilter;
  onTimeFilter: (t: TimeFilter) => void;
  folders: { id: string; name: string }[];
  folderId: string | null;
  onFolderId: (id: string | null) => void;
}) {
  const activeFolder = folders.find((f) => f.id === folderId) ?? null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-4 py-2">
      <div className="flex items-center gap-1">
        {TIME_FILTER_ORDER.map((t) => (
          <button
            key={t}
            type="button"
            aria-pressed={timeFilter === t}
            onClick={() => onTimeFilter(t)}
            className={`rounded-full px-2.5 py-1 text-xs transition-colors ${
              timeFilter === t
                ? "bg-primary/15 text-foreground"
                : "text-muted-foreground hover:bg-accent"
            }`}
          >
            {TIME_FILTER_LABELS[t]}
          </button>
        ))}
      </div>
      {folders.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label="按文件夹过滤"
              className={`ml-auto flex max-w-[12rem] items-center gap-1 rounded-lg px-2 py-1 text-xs transition-colors hover:bg-accent ${
                activeFolder ? "text-foreground" : "text-muted-foreground"
              }`}
            >
              <Folder size={13} className="shrink-0" />
              <span className="min-w-0 truncate">
                {activeFolder ? activeFolder.name : "全部文件夹"}
              </span>
              <ChevronDown size={13} className="shrink-0 opacity-60" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="max-h-72 overflow-y-auto">
            <DropdownMenuItem onSelect={() => onFolderId(null)}>
              <span className="min-w-0 flex-1 truncate">全部文件夹</span>
              {folderId === null && <Check size={14} className="shrink-0" />}
            </DropdownMenuItem>
            {folders.map((f) => (
              <DropdownMenuItem key={f.id} onSelect={() => onFolderId(f.id)}>
                <Folder size={14} className="shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate">{f.name}</span>
                {folderId === f.id && <Check size={14} className="shrink-0" />}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}

/**
 * Global command palette (Ctrl/Cmd+K) — Tier 2: commands + global search.
 *
 * Two result kinds share one list. **Commands** (新建对话 / 跳转页面 / 切换主题
 * 等) are matched client-side from a static registry, so they appear instantly
 * with no round-trip. **Entities** (对话 / 消息 / 文件夹) come from the debounced
 * backend keyword search (Tier 1) for a non-empty query, or the recent
 * conversations list (client-side, 决策④) for an empty one.
 *
 * Ordering: an empty query keeps 最近对话 on top (preserving the quick-switch
 * muscle memory) with commands below; once the user types, matching commands
 * lead and entity hits follow. cmdk owns ↑/↓/Enter navigation and active-item
 * scrolling (its own filtering stays disabled — both kinds are pre-filtered
 * here). Command jumps run the action and close; entity jumps:
 * conversation → open it; message → open + scroll-to-message (load-around for
 * hits outside the window, 命中必达); folder → reveal it on the management page.
 */
export function CommandPalette() {
  const open = useUIStore((s) => s.searchOpen);
  const close = useUIStore((s) => s.closeSearch);
  const theme = useUIStore((s) => s.theme);
  const sidebarCollapsed = useSidebarStore((s) => s.collapsed);
  const switchConversation = useConversationStore((s) => s.switchConversation);
  const navigate = useNavigate();
  const { isNarrow } = useNarrowLayoutState();
  const forceLightTheme = isNarrow || isNativeRuntime();

  const foldersAll = useFolders();
  const folders = useMemo(
    () => dedupeFoldersByLocalBinding(foldersAll),
    [foldersAll],
  );
  const [query, setQuery] = useState("");
  // 搜索结果过滤 (方向 4): time + workspace facets, applied to backend search only.
  const [timeFilter, setTimeFilter] = useState<TimeFilter>("all");
  const [folderId, setFolderId] = useState<string | null>(null);
  const [sections, setSections] = useState<
    { type: SearchSectionType; items: SearchItem[] }[]
  >([]);
  const [loading, setLoading] = useState(false);
  const [errored, setErrored] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  // Guards against out-of-order responses: only the latest keystroke's result
  // is adopted (debounce keeps the request count low, so no abort needed).
  const seqRef = useRef(0);

  // Dev-only 磁带回放目录：服务端开关关闭时 404 → null → 命令面板零可见。
  const { data: demoTapeCatalog } = useQuery({
    queryKey: ["demo-tape-catalog"],
    queryFn: fetchDemoTapeCatalog,
    staleTime: 60_000,
    retry: false,
    enabled: open,
  });
  const demoTapes = demoTapeCatalog?.tapes;

  // Each open adopts an optional prefilled query (e.g. FindBar → global search);
  // otherwise starts empty. Filters reset each open so a session starts unscoped.
  // Input focus is handled by onOpenAutoFocus.
  useEffect(() => {
    if (!open) return;
    const { searchInitialQuery } = useUIStore.getState();
    setQuery(searchInitialQuery);
    setTimeFilter("all");
    setFolderId(null);
    if (searchInitialQuery) {
      useUIStore.setState({
        searchInitialQuery: "",
      });
    }
  }, [open]);

  // Drop a stale workspace scope if that folder vanished (deleted while the last
  // scope lingered in a reopened palette) so search never filters on a dead id.
  useEffect(() => {
    if (folderId && !folders.some((f) => f.id === folderId)) setFolderId(null);
  }, [folderId, folders]);

  // Resolve the query to grouped entity results: empty → recent conversations
  // (local); non-empty → debounced backend search.
  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    if (q.length === 0) {
      seqRef.current++;
      setLoading(false);
      setErrored(false);
      const recent = getConversations()
        .slice(0, PER_TYPE_LIMIT)
        .map((c) => ({ id: c.id, title: c.title }) as SearchItem);
      setSections(
        recent.length ? [{ type: "conversation", items: recent }] : [],
      );
      return;
    }
    setLoading(true);
    setErrored(false);
    const seq = ++seqRef.current;
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const res = await searchAll(q, {
            limit: PER_TYPE_LIMIT,
            updatedAfter: timeFilterSince(timeFilter),
            folderId: folderId ?? undefined,
          });
          if (seq !== seqRef.current) return;
          setSections(
            res.sections as { type: SearchSectionType; items: SearchItem[] }[],
          );
          setLoading(false);
        } catch {
          if (seq !== seqRef.current) return;
          setSections([]);
          setErrored(true);
          setLoading(false);
        }
      })();
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, open, timeFilter, folderId]);

  const isEmptyQuery = query.trim().length === 0;

  // Commands reflect the live UI state (toggle hints, the active theme) and are
  // filtered locally — no backend round-trip, so they show even while a search
  // is in flight or the backend is down.
  const commands = useMemo(
    () =>
      buildPaletteCommands({
        navigate,
        theme,
        sidebarCollapsed,
        demoTapes,
        restrictNarrow: isNarrow,
        forceLightTheme,
      }),
    [navigate, theme, sidebarCollapsed, demoTapes, isNarrow, forceLightTheme],
  );
  const matchedCommands = useMemo(
    () => commands.filter((c) => commandMatches(c, query)),
    [commands, query],
  );

  const commandGroups = useMemo<RenderGroup[]>(
    () =>
      COMMAND_CATEGORY_ORDER.map((cat) => ({
        key: `cmd:${cat}`,
        heading: cat,
        rows: matchedCommands
          .filter((c) => c.category === cat)
          .map((c) => ({ kind: "command", cmd: c }) as Row),
      })).filter((g) => g.rows.length > 0),
    [matchedCommands],
  );

  const entityGroups = useMemo<RenderGroup[]>(
    () =>
      sections
        .filter((s) => s.items.length > 0)
        .map((s) => ({
          key: `ent:${s.type}`,
          heading:
            isEmptyQuery && s.type === "conversation"
              ? "最近对话"
              : SECTION_LABEL[s.type],
          rows: s.items.map(
            (item) => ({ kind: "entity", type: s.type, item }) as Row,
          ),
        })),
    [sections, isEmptyQuery],
  );

  // Empty query → recent conversations, then commands;
  // typing → matching commands first, entity hits after.
  const groups = useMemo<RenderGroup[]>(
    () =>
      isEmptyQuery
        ? [...entityGroups, ...commandGroups]
        : [...commandGroups, ...entityGroups],
    [isEmptyQuery, entityGroups, commandGroups],
  );
  const hasRows = useMemo(
    () => groups.some((g) => g.rows.length > 0),
    [groups],
  );

  const applyTimeFilter = (t: TimeFilter) => {
    setTimeFilter(t);
    inputRef.current?.focus();
  };
  const applyFolderId = (id: string | null) => {
    setFolderId(id);
    inputRef.current?.focus();
  };

  const openConversation = (id: string) => {
    switchConversation(id);
    navigate(`/conversations/${id}`);
    close();
  };

  const openMessage = (item: SearchItem) => {
    const conversationId = item.conversation_id;
    if (!conversationId) return;
    const store = useConversationStore.getState();
    const already = store.currentConversationId === conversationId;
    store.switchConversation(conversationId);
    navigate(`/conversations/${conversationId}`);
    close();
    if (already) {
      // Same conversation already open: navigate is a no-op, so jump now.
      void jumpToMessage(conversationId, item.id);
    } else {
      // Opening fresh: let ConversationPage load its window, then honor this.
      store.requestMessageFocus(conversationId, item.id);
    }
  };

  const openFolder = (id: string) => {
    // A folder with live chats opens the conversation filter and flashes that row.
    // A folder with none is not a filter — open its files. An unloaded grouped
    // cache is not evidence of emptiness, so that case still lands here.
    const groupedSettled =
      queryClient.getQueryData(conversationKeys.grouped) !== undefined;
    const files = filesFocusState(id);
    const noLiveChat = folderHitHasNoLiveChat(
      id,
      getConversations(),
      getFolders(),
      groupedSettled,
    );
    if (files && noLiveChat) {
      navigate("/files", files);
    } else {
      navigate("/conversations", { state: { focusFolderId: id } });
    }
    close();
  };

  const runRow = (row: Row) => {
    if (row.kind === "command") {
      row.cmd.run();
      if (!row.cmd.keepOpen) close();
      return;
    }
    if (row.type === "conversation") openConversation(row.item.id);
    else if (row.type === "message") openMessage(row.item);
    else openFolder(row.item.id);
  };

  const rowValue = (row: Row) => {
    if (row.kind === "command") return `cmd:${row.cmd.id}`;
    return `${row.type}:${row.item.id}`;
  };

  const emptyMessage = (() => {
    if (errored) return "搜索失败，请重试";
    if (loading) return "搜索中…";
    if (isEmptyQuery) return "还没有对话";
    return "没有匹配结果";
  })();

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) close();
      }}
    >
      <DialogContent
        position="top"
        size="xl"
        showClose={false}
        aria-describedby={undefined}
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          inputRef.current?.focus();
        }}
      >
        <DialogTitle className="sr-only">全局搜索与命令</DialogTitle>
        <Command
          label="全局搜索与命令"
          shouldFilter={false}
          loop
          className="flex flex-col overflow-hidden"
        >
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <Search size={16} className="shrink-0 text-muted-foreground" />
            <Command.Input
              ref={inputRef}
              value={query}
              onValueChange={setQuery}
              placeholder="搜索或运行命令…"
              className="w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
            />
            {loading && (
              <Loader2
                size={14}
                className="shrink-0 animate-spin text-muted-foreground"
              />
            )}
            <kbd className="shrink-0 text-xs text-muted-foreground">Esc</kbd>
          </div>

          {!isEmptyQuery && (
            <PaletteFilterBar
              timeFilter={timeFilter}
              onTimeFilter={applyTimeFilter}
              folders={folders}
              folderId={folderId}
              onFolderId={applyFolderId}
            />
          )}

          <Command.List className="max-h-96 overflow-y-auto py-1.5">
            {!hasRows ? (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                {emptyMessage}
              </div>
            ) : (
              groups.map((group) => (
                <Command.Group
                  key={group.key}
                  heading={group.heading}
                  className={GROUP_CLASS}
                >
                  {group.rows.map((row) => {
                    const value = rowValue(row);
                    if (row.kind === "command") {
                      const Icon = row.cmd.icon;
                      return (
                        <Command.Item
                          key={value}
                          value={value}
                          onSelect={() => runRow(row)}
                          className={ROW_CLASS}
                        >
                          <Icon
                            size={16}
                            className="shrink-0 text-muted-foreground"
                          />
                          <span className="min-w-0 flex-1 truncate text-sm">
                            {row.cmd.title}
                          </span>
                          {row.cmd.shortcut ? (
                            <kbd className="shrink-0 text-xs text-muted-foreground">
                              {row.cmd.shortcut}
                            </kbd>
                          ) : row.cmd.hint ? (
                            <span className="shrink-0 text-xs text-muted-foreground">
                              {row.cmd.hint}
                            </span>
                          ) : null}
                        </Command.Item>
                      );
                    }
                    const Icon = SECTION_ICON[row.type];
                    const isMessage = row.type === "message";
                    return (
                      <Command.Item
                        key={value}
                        value={value}
                        onSelect={() => runRow(row)}
                        className={ROW_CLASS}
                      >
                        <Icon
                          size={16}
                          className="shrink-0 text-muted-foreground"
                        />
                        <span className="flex min-w-0 flex-1 flex-col">
                          <span className="truncate text-sm">
                            {row.item.title || "未命名"}
                          </span>
                          {isMessage && row.item.snippet && (
                            <span className="flex text-xs">
                              <Snippet item={row.item} />
                            </span>
                          )}
                        </span>
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              ))
            )}
          </Command.List>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
