import { DirTypeIcon, FileTypeIcon } from "@/components/files/FileTypeIcon";
import { Button, SearchField } from "@/components/ui";
import { hasLocalFiles } from "@/lib/capabilities";
import type { IndexedEntry } from "@/lib/fileIndex";
import {
  Bookmark,
  ChevronLeft,
  ChevronRight,
  File,
  Folder,
  FolderPlus,
  MessageSquare,
  Paperclip,
  Users,
} from "lucide-react";
import { useEffect, useRef } from "react";
import type { MentionSectionId } from "./message-input/composerAttachments";
import type {
  MentionCategoryId,
  MentionCategoryRow,
} from "./message-input/mentionMenuLevel";
import {
  MENTION_LIST_TRUNCATED_HINT,
  isMentionSectionId,
} from "./message-input/mentionMenuLevel";

export type MentionMenuSelectable =
  | { kind: "agent"; agentId: string; role: string }
  | IndexedEntry;

export interface MentionMenuSection {
  id: MentionSectionId;
  label: string;
  items: MentionMenuSelectable[];
  /** 分区无候选时的一行软提示（团队空态）。 */
  emptyHint?: string;
  /** 列表被条数上限或源索引截断。 */
  truncated?: boolean;
}

interface Props {
  sections: MentionMenuSection[];
  /** 扁平可选项（与键盘 activeIndex 对齐，跳过 header/hint）。 */
  flatItems: MentionMenuSelectable[];
  activeIndex: number;
  loading: boolean;
  error: string | null;
  query: string;
  /** browse 模式自带搜索框；mention 模式由输入框 @query 过滤。 */
  showSearch: boolean;
  /** 无本地/云文件来源时，在文件夹分区提供「添加目录」。 */
  noFileSources: boolean;
  /** 空查询：只渲染一级目录。 */
  showCategoryLevel: boolean;
  categories: MentionCategoryRow[];
  /** 已钻入或带类型前缀时，二级顶栏返回。 */
  canGoBack: boolean;
  focusedSectionLabel?: string;
  onQueryChange: (q: string) => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
  onSelect: (item: MentionMenuSelectable) => void;
  onHover: (index: number) => void;
  onDrill: (id: MentionSectionId) => void;
  /** 一级「附件」：本机选文件，不 drill。 */
  onAttach: () => void;
  onBack: () => void;
  onAddRoot: () => void;
  searchInputRef: React.RefObject<HTMLInputElement | null>;
  /** card 朝下；bar 贴底朝上。 */
  placement?: "above" | "below";
}

function isAgent(
  item: MentionMenuSelectable,
): item is { kind: "agent"; agentId: string; role: string } {
  return "kind" in item && item.kind === "agent" && "agentId" in item;
}

function itemKey(item: MentionMenuSelectable): string {
  if (isAgent(item)) return `agent:${item.agentId}`;
  return `${item.kind}:${item.sourceId}:${item.relPath}`;
}

function categoryIcon(id: MentionCategoryId) {
  switch (id) {
    case "attach":
      return <Paperclip size={14} className="shrink-0 text-muted-foreground" />;
    case "team":
      return <Users size={14} className="shrink-0 text-muted-foreground" />;
    case "conversation":
      return (
        <MessageSquare size={14} className="shrink-0 text-muted-foreground" />
      );
    case "setting":
      return <Bookmark size={14} className="shrink-0 text-muted-foreground" />;
    case "folder":
      return <Folder size={14} className="shrink-0 text-muted-foreground" />;
    case "file":
      return <File size={14} className="shrink-0 text-muted-foreground" />;
  }
}

function categoryMeta(row: MentionCategoryRow): string | undefined {
  if (row.id === "attach") return row.hint;
  if (row.disabled) return row.hint;
  if (row.loading) return "…";
  if (row.count > 0) return String(row.count);
  return "0";
}

export function MentionMenu({
  sections,
  flatItems,
  activeIndex,
  loading,
  error,
  query,
  showSearch,
  noFileSources,
  showCategoryLevel,
  categories,
  canGoBack,
  focusedSectionLabel,
  onQueryChange,
  onKeyDown,
  onSelect,
  onHover,
  onDrill,
  onAttach,
  onBack,
  onAddRoot,
  searchInputRef,
  placement = "below",
}: Props) {
  const listRef = useRef<HTMLUListElement>(null);

  // 键盘移动高亮时，把当前项滚入可视区。
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const el = list.querySelector(
      `[data-mention-flat="${activeIndex}"]`,
    ) as HTMLElement | null;
    el?.scrollIntoView?.({ block: "nearest" });
  }, [activeIndex]);

  const hasAnyItem = flatItems.length > 0;
  const hasSectionHints = sections.some(
    (s) => s.emptyHint && s.items.length === 0,
  );
  const visibleSections = sections.filter((section) => {
    if (section.items.length > 0) return true;
    if (section.emptyHint) return true;
    if (
      (section.id === "file" || section.id === "folder") &&
      noFileSources &&
      hasLocalFiles()
    ) {
      return section.id === "folder";
    }
    return false;
  });
  const showEmpty = !loading && visibleSections.length === 0;

  let flatCursor = 0;

  return (
    <div
      className={
        placement === "above"
          ? "absolute bottom-full left-0 z-20 mb-2 w-full overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-lg"
          : "absolute top-full left-0 z-20 mt-2 w-full overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-lg"
      }
      data-mention-level={showCategoryLevel ? "categories" : "items"}
    >
      {showSearch && (
        <div className="border-b border-border px-3 py-2">
          <SearchField
            ref={searchInputRef}
            variant="plain"
            value={query}
            onValueChange={onQueryChange}
            onKeyDown={onKeyDown}
            placeholder="筛选文件、对话或角色…"
            aria-label="筛选文件、对话或角色"
            clearable={false}
            escapeClears={false}
          />
        </div>
      )}

      {canGoBack && (
        <div className="border-b border-border">
          <Button
            variant="ghost"
            onMouseDown={(e) => {
              e.preventDefault();
              onBack();
            }}
            className="h-auto w-full justify-start gap-2 rounded-none px-3 py-1.5 text-sm font-normal text-muted-foreground hover:bg-accent hover:text-foreground"
            aria-label={`返回目录${focusedSectionLabel ? `（${focusedSectionLabel}）` : ""}`}
          >
            <ChevronLeft size={14} className="shrink-0" />
            <span>{focusedSectionLabel ?? "返回"}</span>
          </Button>
        </div>
      )}

      {showCategoryLevel ? (
        <ul ref={listRef} className="max-h-64 overflow-y-auto py-1">
          {categories.map((row, index) => (
            <li key={row.id} className="list-none">
              <Button
                variant="ghost"
                data-mention-flat={index}
                data-mention-category={row.id}
                disabled={row.disabled}
                onMouseDown={(e) => {
                  e.preventDefault();
                  if (row.disabled) return;
                  if (row.id === "attach") {
                    onAttach();
                    return;
                  }
                  if (isMentionSectionId(row.id)) onDrill(row.id);
                }}
                onMouseEnter={() => onHover(index)}
                className={`h-auto w-full justify-start gap-2 rounded-none px-3 py-1.5 text-sm font-normal disabled:opacity-60 ${
                  index === activeIndex
                    ? "bg-accent text-accent-foreground"
                    : "text-foreground hover:bg-accent"
                }`}
              >
                <span className="flex w-full items-center gap-2 text-left">
                  {categoryIcon(row.id)}
                  <span className="shrink-0">{row.label}</span>
                  <span className="ml-auto truncate text-xs text-muted-foreground">
                    {categoryMeta(row)}
                  </span>
                  {!row.disabled && row.id !== "attach" && (
                    <ChevronRight
                      size={14}
                      className="shrink-0 text-muted-foreground"
                    />
                  )}
                </span>
              </Button>
              {row.id === "attach" && (
                <div
                  data-mention-attach-sep
                  className="my-1 border-t border-border"
                />
              )}
            </li>
          ))}
        </ul>
      ) : loading && !hasAnyItem && !hasSectionHints ? (
        <div className="px-3 py-4 text-center text-sm text-muted-foreground">
          正在索引文件…
        </div>
      ) : showEmpty ? (
        <div className="px-3 py-4 text-center text-sm text-muted-foreground">
          {query.trim()
            ? "没有匹配的文件、目录、对话或角色"
            : "暂无可引用的内容"}
        </div>
      ) : (
        <ul ref={listRef} className="max-h-64 overflow-y-auto py-1">
          {visibleSections.map((section) => {
            const showHint =
              section.items.length === 0 && Boolean(section.emptyHint);
            const showFileEmptyAdd =
              section.id === "folder" &&
              section.items.length === 0 &&
              noFileSources &&
              hasLocalFiles();
            return (
              <li key={section.id} className="list-none">
                {!canGoBack && (
                  <div className="px-3 pb-0.5 pt-1.5 text-xs font-medium text-muted-foreground">
                    {section.label}
                  </div>
                )}
                {showHint && (
                  <div className="px-3 py-1.5 text-xs text-muted-foreground">
                    {section.emptyHint}
                  </div>
                )}
                {showFileEmptyAdd && (
                  <div className="px-3 py-2 text-center">
                    <p className="text-sm text-muted-foreground">
                      还没有授权目录
                    </p>
                    <Button
                      variant="neutral"
                      onClick={onAddRoot}
                      className="mt-2 bg-accent text-accent-foreground hover:bg-accent/80"
                      icon={<FolderPlus size={14} />}
                    >
                      添加目录
                    </Button>
                  </div>
                )}
                {section.items.map((item) => {
                  const flatIndex = flatCursor;
                  flatCursor += 1;
                  return (
                    <div key={itemKey(item)} className="px-0">
                      <Button
                        variant="ghost"
                        data-mention-flat={flatIndex}
                        onMouseDown={(e) => {
                          // mousedown 抢在 textarea blur 之前，避免菜单先收起。
                          e.preventDefault();
                          onSelect(item);
                        }}
                        onMouseEnter={() => onHover(flatIndex)}
                        className={`h-auto w-full justify-start gap-2 rounded-none px-3 py-1.5 text-sm font-normal ${
                          flatIndex === activeIndex
                            ? "bg-accent text-accent-foreground"
                            : "text-foreground hover:bg-accent"
                        }`}
                      >
                        <span className="flex w-full items-center gap-2 text-left">
                          {isAgent(item) ? (
                            <Users
                              size={14}
                              className="shrink-0 text-muted-foreground"
                            />
                          ) : item.kind === "dir" ? (
                            <DirTypeIcon
                              name={item.name}
                              path={item.relPath}
                              size={14}
                            />
                          ) : item.kind === "conversation" ? (
                            <MessageSquare
                              size={14}
                              className="shrink-0 text-muted-foreground"
                            />
                          ) : item.kind === "document" ? (
                            <Bookmark
                              size={14}
                              className="shrink-0 text-muted-foreground"
                            />
                          ) : (
                            <FileTypeIcon
                              name={item.name}
                              path={item.relPath}
                              size={14}
                            />
                          )}
                          <span className="shrink-0 truncate">
                            {isAgent(item)
                              ? item.role
                              : `${item.name}${item.kind === "dir" ? "/" : ""}`}
                          </span>
                          <span className="ml-auto truncate text-xs text-muted-foreground">
                            {isAgent(item)
                              ? "点名"
                              : item.kind === "conversation"
                                ? "对话"
                                : item.kind === "document"
                                  ? "设定"
                                  : item.display}
                          </span>
                        </span>
                      </Button>
                    </div>
                  );
                })}
                {section.truncated && section.items.length > 0 && (
                  <div
                    data-mention-truncated={section.id}
                    className="px-3 py-1.5 text-xs text-muted-foreground"
                  >
                    {MENTION_LIST_TRUNCATED_HINT}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {error && (
        <div className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
          {error}
        </div>
      )}
    </div>
  );
}
