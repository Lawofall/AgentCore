import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Page, PageHeader } from "@/components/ui/Page";
import { Pagination } from "@/components/ui/Pagination";
import { Select } from "@/components/ui/Select";
import {
  EmptyState,
  ErrorState,
  Refreshing,
  TableSkeleton,
} from "@/components/ui/States";
import {
  TableFrame,
  TableMessageRow,
  TableRow,
  THead,
  Td,
  Th,
} from "@/components/ui/Table";
import { useAdminListPage } from "@/hooks/useAdminListPage";
import { useDebouncedUrlText } from "@/hooks/useDebouncedUrlText";
import { useFirstLoad } from "@/hooks/useFirstLoad";
import { bool, date, oneOf, str, useUrlFilters } from "@/hooks/useUrlFilters";
import {
  cn,
  fmtCny,
  fmtInt,
  fmtTime,
  nanoToYuan,
} from "@/lib/utils";
import {
  type AdminConversationListItem,
  type ConversationSort,
  type SortOrder,
  listConversations,
  listTurns,
} from "@/services/adminConversations";
import { errorMessage } from "@/services/api";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  MessageSquare,
  RefreshCw,
  Search,
  Users,
} from "lucide-react";
import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

const PAGE_SIZE = 20;

/**
 * Filters live in the URL so a narrowed view is bookmarkable and survives a reload; see
 * `useUrlFilters` for the shared conventions. Param names follow the backend query fields.
 */
const CONVERSATION_FILTERS = {
  q: str(),
  has_errors: oneOf(["all", "yes", "no"] as const, "all"),
  has_delegated: oneOf(["all", "yes", "no"] as const, "all"),
  include_deleted: bool(true),
  since: date(),
  until: date(),
  sort: oneOf(
    ["updated_at", "created_at", "cost", "delegated"] as const,
    "updated_at",
  ),
  order: oneOf(["asc", "desc"] as const, "desc"),
};

const ERROR_OPTIONS = [
  { value: "all", label: "全部会话" },
  { value: "yes", label: "仅有错误" },
  { value: "no", label: "无错误" },
];

const DELEGATED_OPTIONS = [
  { value: "all", label: "全部协作" },
  { value: "yes", label: "仅多 Agent" },
  { value: "no", label: "无委派" },
];

/** UTC day bounds for ``since`` / ``until`` query params (date input → ISO). */
function dateToSince(isoDate: string): string {
  return `${isoDate}T00:00:00.000Z`;
}

function dateToUntil(isoDate: string): string {
  return `${isoDate}T23:59:59.999Z`;
}

/**
 * Header slots owned by the page; each segment panel supplies its own filter row.
 *
 * `jump` is a header control but rides in the filter row rather than `actions`:
 * `PageHeader` marks its action cluster `shrink-0`, so a row that can't fit is laid
 * out at max-content and overflows the viewport instead of wrapping — the ID box +
 * 复盘 button pushed the whole 对话 page into horizontal scroll at 375 and left the
 * button off-screen. The filter row wraps.
 */
interface PanelShell {
  description?: string;
  note?: ReactNode;
  jump: ReactNode;
}

/** Checkbox filter dressed as a field so it lines up with the selects beside it. */
function CheckboxFilter({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label
      className={cn(
        "flex h-9 items-center gap-2 rounded-lg border border-input bg-card px-3 text-muted-foreground text-sm",
        disabled && "opacity-50",
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="size-4 rounded border-input accent-primary"
      />
      {label}
    </label>
  );
}

function SortHeader({
  label,
  active,
  order,
  align = "left",
  onClick,
}: {
  label: string;
  active: boolean;
  order: SortOrder;
  align?: "left" | "right";
  onClick: () => void;
}) {
  const Icon = !active ? ArrowUpDown : order === "asc" ? ArrowUp : ArrowDown;
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 rounded font-medium outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
        active && "text-foreground",
        align === "right" && "flex-row-reverse",
      )}
      aria-label={`按${label}排序`}
    >
      {label}
      <Icon size={12} className={cn(!active && "opacity-50")} />
    </button>
  );
}

/**
 * 对话: platform-wide AI conversation index. Rows drill into 会话复盘.
 * Paste conversation_id or trace_id to jump; turns API is only used to resolve traces.
 */
export function ConversationsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [jumpInput, setJumpInput] = useState("");
  const [jumpBusy, setJumpBusy] = useState(false);

  const userIdFilter = searchParams.get("user_id") ?? undefined;

  const openReplay = (conversationId: string, opts?: { trace?: string }) => {
    const qs = opts?.trace
      ? `?trace=${encodeURIComponent(opts.trace)}`
      : "";
    navigate(`/replay/${conversationId}${qs}`, {
      state: { from: `${location.pathname}${location.search}` },
    });
  };

  const submitJump = async (e: FormEvent) => {
    e.preventDefault();
    const raw = jumpInput.trim();
    if (!raw) return;
    setJumpBusy(true);
    try {
      // Prefer trace_id resolution (32-hex) → conversation + highlight; else treat as
      // conversation_id (same shape as many ids — operator paste either works).
      if (/^[0-9a-f]{32}$/i.test(raw)) {
        const byTrace = await listTurns({
          page: 1,
          pageSize: 1,
          traceId: raw,
        });
        const hit = byTrace.data[0];
        if (hit) {
          openReplay(hit.conversation_id, { trace: raw });
          return;
        }
      }
      openReplay(raw);
    } catch (err) {
      // Fall through to conversation_id open; replay page surfaces 404.
      void err;
      openReplay(raw);
    } finally {
      setJumpBusy(false);
    }
  };

  const clearUserFilter = () => {
    const next = new URLSearchParams(searchParams);
    next.delete("user_id");
    setSearchParams(next);
  };

  const shell: PanelShell = {
    note: userIdFilter ? (
      <>
        仅看用户{" "}
        <code className="rounded bg-muted px-1.5 py-0.5 font-mono">
          {userIdFilter}
        </code>
        <button
          type="button"
          onClick={clearUserFilter}
          className="ml-2 rounded text-primary underline-offset-2 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          清除
        </button>
      </>
    ) : undefined,
    jump: (
      <form
        onSubmit={(e) => void submitJump(e)}
        className="flex w-full items-center gap-2 sm:ml-auto sm:w-auto"
      >
        <Input
          value={jumpInput}
          onChange={(e) => setJumpInput(e.target.value)}
          placeholder="conversation_id / trace_id…"
          aria-label="按 ID 打开复盘"
          className="min-w-0 flex-1 font-mono text-xs sm:w-56 sm:flex-none"
        />
        <Button
          type="submit"
          variant="outline"
          size="sm"
          disabled={!jumpInput.trim() || jumpBusy}
        >
          复盘
        </Button>
      </form>
    ),
  };

  return (
    <Page>
      <ConversationsPanel
        shell={shell}
        userId={userIdFilter}
        onOpenReplay={openReplay}
      />
    </Page>
  );
}

function ConversationsPanel({
  shell,
  userId,
  onOpenReplay,
}: {
  shell: PanelShell;
  userId?: string;
  onOpenReplay: (id: string, opts?: { trace?: string }) => void;
}) {
  const [rows, setRows] = useState<AdminConversationListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useAdminListPage();
  const { values, set } = useUrlFilters(CONVERSATION_FILTERS);
  const {
    // The URL holds the *debounced* search text; the box below echoes keystrokes.
    q: debouncedQ,
    has_errors: hasErrors,
    has_delegated: hasDelegated,
    include_deleted: includeDeleted,
    since: sinceDate,
    until: untilDate,
    sort,
    order,
  } = values;
  const [q, setQ] = useDebouncedUrlText(debouncedQ, (next) => set({ q: next }));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const skipUserIdPageReset = useRef(true);
  // Debounced search + filter/page changes can overlap; only the latest response wins.
  const loadGenRef = useRef(0);
  const loadAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (skipUserIdPageReset.current) {
      skipUserIdPageReset.current = false;
      return;
    }
    setPage(1);
  }, [userId, setPage]);

  const load = useCallback(async () => {
    loadAbortRef.current?.abort();
    const ac = new AbortController();
    loadAbortRef.current = ac;
    const gen = ++loadGenRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await listConversations(
        {
          page,
          pageSize: PAGE_SIZE,
          q: debouncedQ,
          userId,
          hasErrors:
            hasErrors === "yes" ? true : hasErrors === "no" ? false : undefined,
          hasDelegated:
            hasDelegated === "yes"
              ? true
              : hasDelegated === "no"
                ? false
                : undefined,
          includeDeleted,
          since: sinceDate ? dateToSince(sinceDate) : undefined,
          until: untilDate ? dateToUntil(untilDate) : undefined,
          sort,
          order,
        },
        ac.signal,
      );
      if (ac.signal.aborted || gen !== loadGenRef.current) return;
      setRows(res.data);
      setTotal(res.total);
    } catch (err) {
      if (ac.signal.aborted || gen !== loadGenRef.current) return;
      setError(errorMessage(err));
    } finally {
      if (!ac.signal.aborted && gen === loadGenRef.current) {
        setLoading(false);
      }
    }
  }, [
    page,
    debouncedQ,
    userId,
    hasErrors,
    hasDelegated,
    includeDeleted,
    sinceDate,
    untilDate,
    sort,
    order,
  ]);

  const toggleSort = useCallback(
    (key: ConversationSort) => {
      // Column and direction move together in one navigation — writing them separately
      // would render (and fetch) a sort the operator never asked for.
      if (sort === key) set({ order: order === "asc" ? "desc" : "asc" });
      else set({ sort: key, order: "desc" });
    },
    [sort, order, set],
  );

  useEffect(() => {
    void load();
    return () => {
      loadAbortRef.current?.abort();
    };
  }, [load]);

  const firstLoad = useFirstLoad(loading);

  return (
    <>
      <PageHeader
        title="对话"
        description={shell.description}
        note={shell.note}
        filters={
          <>
            <div className="relative w-full max-w-[16rem]">
              <Search
                size={14}
                aria-hidden
                className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                disabled={firstLoad}
                placeholder="搜索会话标题…"
                aria-label="搜索会话标题"
                className="pl-8"
              />
            </div>
            <Select
              aria-label="按错误筛选"
              value={hasErrors}
              disabled={firstLoad}
              options={ERROR_OPTIONS}
              onChange={(e) =>
                set({ has_errors: e.target.value as "all" | "yes" | "no" })
              }
            />
            <Select
              aria-label="按协作筛选"
              value={hasDelegated}
              disabled={firstLoad}
              options={DELEGATED_OPTIONS}
              onChange={(e) =>
                set({ has_delegated: e.target.value as "all" | "yes" | "no" })
              }
            />
            <Input
              type="date"
              value={sinceDate}
              disabled={firstLoad}
              onChange={(e) => set({ since: e.target.value })}
              className="w-36"
              title="更新起始（UTC 日）"
              aria-label="更新起始日期"
            />
            <Input
              type="date"
              value={untilDate}
              disabled={firstLoad}
              onChange={(e) => set({ until: e.target.value })}
              className="w-36"
              title="更新截止（UTC 日）"
              aria-label="更新截止日期"
            />
            <CheckboxFilter
              label="含已删除"
              checked={includeDeleted}
              disabled={firstLoad}
              onChange={(checked) => set({ include_deleted: checked })}
            />
            <Button
              variant="outline"
              size="sm"
              onClick={() => void load()}
              disabled={loading}
              aria-label="刷新"
            >
              <RefreshCw size={14} className={cn(loading && "animate-spin")} />
            </Button>
            {shell.jump}
          </>
        }
      />

      {firstLoad ? (
        <TableSkeleton rows={8} columns={6} />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : (
        <Refreshing active={loading}>
          <TableFrame minWidth={1080}>
            <THead>
              <Th className="whitespace-nowrap">标题</Th>
              <Th className="whitespace-nowrap">用户</Th>
              <Th align="right" className="whitespace-nowrap">
                消息
              </Th>
              <Th align="right" className="whitespace-nowrap">
                回合
              </Th>
              <Th align="right" className="whitespace-nowrap">
                错误
              </Th>
              <Th align="right" className="whitespace-nowrap">
                <SortHeader
                  label="委派"
                  active={sort === "delegated"}
                  order={order}
                  align="right"
                  onClick={() => toggleSort("delegated")}
                />
              </Th>
              <Th align="right" className="whitespace-nowrap">
                <SortHeader
                  label="成本"
                  active={sort === "cost"}
                  order={order}
                  align="right"
                  onClick={() => toggleSort("cost")}
                />
              </Th>
              <Th className="whitespace-nowrap">
                <SortHeader
                  label="创建"
                  active={sort === "created_at"}
                  order={order}
                  onClick={() => toggleSort("created_at")}
                />
              </Th>
              <Th className="whitespace-nowrap">
                <SortHeader
                  label="更新"
                  active={sort === "updated_at"}
                  order={order}
                  onClick={() => toggleSort("updated_at")}
                />
              </Th>
            </THead>
            <tbody>
              {rows.length === 0 ? (
                <TableMessageRow colSpan={9}>
                  <EmptyState
                    className="py-0"
                    icon={MessageSquare}
                    title="暂无会话"
                    description="没有会话命中当前筛选，换个条件或时间范围再试。"
                  />
                </TableMessageRow>
              ) : (
                rows.map((c) => (
                  <TableRow
                    key={c.id}
                    className="align-top"
                    label={`打开复盘：${c.title || "未命名会话"}`}
                    onActivate={() => onOpenReplay(c.id)}
                  >
                    <Td className="max-w-xs text-foreground">
                      <div className="line-clamp-2 break-words">
                        {c.title || (
                          <span className="text-muted-foreground italic">
                            未命名会话
                          </span>
                        )}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {(c.delegated_turns ?? 0) > 0 && (
                          <Badge tone="primary">
                            <Users size={10} className="mr-0.5" />多 Agent
                            {(c.workers ?? 0) > 0 ? ` · ${c.workers}` : ""}
                          </Badge>
                        )}
                        {c.deleted_at && <Badge tone="neutral">会话已删</Badge>}
                        {c.user_deleted_at && (
                          <Badge tone="warning">用户已注销</Badge>
                        )}
                      </div>
                    </Td>
                    <Td>
                      <div className="font-medium text-foreground">
                        {c.display_name || c.username || "—"}
                      </div>
                      {c.username && (
                        <div className="text-muted-foreground text-xs">
                          @{c.username}
                        </div>
                      )}
                    </Td>
                    <Td align="right" className="text-muted-foreground tabular-nums">
                      {fmtInt(c.messages)}
                    </Td>
                    <Td align="right" className="text-muted-foreground tabular-nums">
                      {fmtInt(c.turns)}
                    </Td>
                    <Td align="right" className="tabular-nums">
                      {c.errors > 0 ? (
                        <span className="text-destructive">
                          {fmtInt(c.errors)}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">0</span>
                      )}
                    </Td>
                    <Td align="right" className="text-muted-foreground tabular-nums">
                      {(c.delegated_turns ?? 0) > 0
                        ? fmtInt(c.delegated_turns)
                        : "—"}
                    </Td>
                    <Td align="right" className="text-muted-foreground tabular-nums">
                      {c.cost_total > 0 ? fmtCny(nanoToYuan(c.cost_total)) : "—"}
                    </Td>
                    <Td className="whitespace-nowrap text-muted-foreground tabular-nums">
                      {fmtTime(c.created_at)}
                    </Td>
                    <Td className="whitespace-nowrap text-muted-foreground tabular-nums">
                      {fmtTime(c.updated_at)}
                    </Td>
                  </TableRow>
                ))
              )}
            </tbody>
          </TableFrame>
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={total}
            onPageChange={setPage}
            disabled={loading}
          />
        </Refreshing>
      )}
    </>
  );
}
