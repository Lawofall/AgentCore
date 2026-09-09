import { QuotaDialog } from "@/components/QuotaDialog";
import { UserDetail } from "@/components/UserDetail";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Page, PageHeader } from "@/components/ui/Page";
import { Pagination } from "@/components/ui/Pagination";
import { Select } from "@/components/ui/Select";
import { Spinner } from "@/components/ui/Spinner";
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
import { cn, fmtCny, fmtCount, fmtInt, nanoToYuan } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import {
  type AdminUser,
  type AdminUserListItem,
  type SortOrder,
  type UserRole,
  type UserSort,
  type UserStatus,
  deleteUser,
  listUsers,
  updateUser,
} from "@/services/adminUsers";
import { useAdminListPage } from "@/hooks/useAdminListPage";
import { useDebouncedUrlText } from "@/hooks/useDebouncedUrlText";
import { useFirstLoad } from "@/hooks/useFirstLoad";
import { bool, date, oneOf, str, useUrlFilters } from "@/hooks/useUrlFilters";
import { useAuthStore } from "@/stores/auth";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  RefreshCw,
  Search,
  Users,
} from "lucide-react";
import {
  type MouseEvent as ReactMouseEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

const PAGE_SIZE = 20;
const COLUMNS = 7;
/** Keystrokes settle before they reach the URL (and the API). */
/**
 * Filter + sort state lives in the query string so a narrowed roster is a link: the
 * operator can bookmark it, paste it to a colleague, and reload onto the same view.
 * Param names mirror the backend's own query fields and defaults stay out of the URL —
 * see `useUrlFilters`, whose contract also requires this to be a module-level constant.
 */
const USER_FILTERS = {
  q: str(),
  ip: str(),
  since: date(),
  until: date(),
  // "all" = dimension unpinned (no query param sent).
  role: oneOf(["all", "user", "admin"] as const, "all"),
  status: oneOf(["all", "active", "disabled"] as const, "all"),
  // Newest registration first by default; 累计成本 is the other axis.
  sort: oneOf(["created_at", "cost"] as const, "created_at"),
  order: oneOf(["asc", "desc"] as const, "desc"),
  // Off by default: 注销 accounts are anonymized tombstones, shown only for audit.
  include_deleted: bool(false),
};

/** UTC day bounds for ``since`` / ``until`` query params (date input → ISO). */
function dateToSince(isoDate: string): string {
  return `${isoDate}T00:00:00.000Z`;
}

function dateToUntil(isoDate: string): string {
  return `${isoDate}T23:59:59.999Z`;
}

/**
 * ISO → "YYYY-MM-DD" in **UTC**, the same day boundary `since` / `until` cut on.
 * Rendering the column in local time made a row filtered in by `since=06-01`
 * read as 05-31 for anyone west of UTC.
 */
function fmtDateUtc(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`;
}

/** The four dimensions a per-account override can pin; `null` = 用全局默认值. */
type QuotaOverrides = Pick<
  AdminUserListItem,
  | "is_unlimited"
  | "quota_daily_tokens"
  | "quota_daily_requests"
  | "quota_daily_cost_cny"
  | "quota_monthly_cost_cny"
>;

/**
 * 配额列：只列这个账号自己覆盖掉的维度，剩下的用一句「其余继承」带过。
 *
 * 逐维打印继承值会把「继承」重复三遍、还把上限写成裸数字——「日 20000000 token ·
 * 月 继承 · 继承 请求」既读不通，也让这一列在 1440 下和用户列抢宽度。
 */
function quotaSummary(u: QuotaOverrides): string {
  if (u.is_unlimited) return "无限额";
  const parts: string[] = [];
  if (u.quota_daily_tokens !== null) {
    parts.push(`日 ${fmtInt(u.quota_daily_tokens)} token`);
  }
  if (u.quota_daily_requests !== null) {
    parts.push(`日 ${fmtInt(u.quota_daily_requests)} 次请求`);
  }
  if (u.quota_daily_cost_cny !== null) {
    parts.push(`日 ${fmtCny(u.quota_daily_cost_cny)}`);
  }
  if (u.quota_monthly_cost_cny !== null) {
    parts.push(`月 ${fmtCny(u.quota_monthly_cost_cny)}`);
  }
  if (parts.length === 0) return "继承默认";
  if (parts.length === 4) return parts.join(" · ");
  return `${parts.join(" · ")} · 其余继承`;
}

const ROLE_FILTER_OPTIONS = [
  { value: "all", label: "全部角色" },
  { value: "user", label: "user" },
  { value: "admin", label: "admin" },
];

const STATUS_FILTER_OPTIONS = [
  { value: "all", label: "全部状态" },
  { value: "active", label: "活跃" },
  { value: "disabled", label: "已停用" },
];

const ROLE_OPTIONS = [
  { value: "user", label: "user" },
  { value: "admin", label: "admin" },
];

/** A clickable column header: shows the active sort direction, neutral otherwise. */
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

/** Cells holding their own controls must not also fire the row's drill-in. */
function stopRowActivation(e: ReactMouseEvent) {
  e.stopPropagation();
}

export function UsersPage() {
  const { userId: detailId } = useParams<{ userId?: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const selfId = useAuthStore((s) => s.user?.id);
  const [users, setUsers] = useState<AdminUserListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useAdminListPage();
  const { values, set } = useUrlFilters(USER_FILTERS);
  const {
    q,
    ip,
    since,
    until,
    role,
    status,
    sort,
    order,
    include_deleted: includeDeleted,
  } = values;
  const [qInput, setQInput] = useDebouncedUrlText(q, (next) => set({ q: next }));
  const [ipInput, setIpInput] = useDebouncedUrlText(ip, (next) =>
    set({ ip: next }),
  );
  const [loading, setLoading] = useState(true);
  // Flips on the first successful load: before that a fetch is a first paint
  // (skeleton), after it a refresh (keep the old rows, dim them).
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<AdminUser | null>(null);
  // The account the operator is about to 注销 (null = no dialog open).
  const [deleting, setDeleting] = useState<AdminUser | null>(null);
  // Debounced filter + page clamp can fire two loads; only the latest response wins.
  const loadGenRef = useRef(0);
  const loadAbortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    loadAbortRef.current?.abort();
    const ac = new AbortController();
    loadAbortRef.current = ac;
    const gen = ++loadGenRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await listUsers(
        {
          page,
          pageSize: PAGE_SIZE,
          q,
          role: role === "all" ? undefined : role,
          status: status === "all" ? undefined : status,
          ip,
          since: since ? dateToSince(since) : undefined,
          until: until ? dateToUntil(until) : undefined,
          sort,
          order,
          includeDeleted,
        },
        ac.signal,
      );
      if (ac.signal.aborted || gen !== loadGenRef.current) return;
      setUsers(res.data);
      setTotal(res.total);
      setLoaded(true);
    } catch (err) {
      if (ac.signal.aborted || gen !== loadGenRef.current) return;
      setError(errorMessage(err));
    } finally {
      if (!ac.signal.aborted && gen === loadGenRef.current) {
        setLoading(false);
      }
    }
  }, [page, q, ip, since, until, role, status, sort, order, includeDeleted]);

  // Flip a column's sort: re-click toggles direction; a new key starts desc.
  // Any sort change restarts at page 1 (offset pagination over a new ordering) — `set`
  // drops `?page=` in the same navigation.
  const toggleSort = useCallback(
    (key: UserSort) => {
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

  const patchRow = useCallback(
    async (
      user: AdminUser,
      patch: Parameters<typeof updateUser>[1],
      okMsg: string,
    ) => {
      setPending((prev) => new Set(prev).add(user.id));
      try {
        const updated = await updateUser(user.id, patch);
        setUsers((prev) =>
          prev.map((u) => (u.id === user.id ? { ...u, ...updated } : u)),
        );
        toast.success(okMsg);
      } catch (err) {
        toast.error(errorMessage(err));
      } finally {
        setPending((prev) => {
          const next = new Set(prev);
          next.delete(user.id);
          return next;
        });
      }
    },
    [],
  );

  // After 注销: the default roster hides tombstones, so drop the row (and adjust the
  // count); in the audit view, swap in the returned tombstone so its state is honest.
  // If the last row on page>1 is removed, clamp page so we don't sit on an empty page.
  const onDeleted = useCallback(
    (updated: AdminUser) => {
      if (includeDeleted) {
        setUsers((prev) =>
          prev.map((u) => (u.id === updated.id ? { ...u, ...updated } : u)),
        );
        setDeleting(null);
        return;
      }
      const nextTotal = Math.max(0, total - 1);
      setUsers((prev) => {
        const next = prev.filter((u) => u.id !== updated.id);
        if (next.length === 0 && page > 1) {
          setPage(Math.max(1, Math.ceil(nextTotal / PAGE_SIZE)));
        }
        return next;
      });
      setTotal(nextTotal);
      setDeleting(null);
    },
    [includeDeleted, total, page, setPage],
  );

  const filtered =
    q !== "" ||
    ip !== "" ||
    since !== "" ||
    until !== "" ||
    role !== "all" ||
    status !== "all" ||
    includeDeleted;

  // Sort is not a filter: clearing 筛选 must not also throw away the operator's ordering,
  // so this patches the filter keys instead of `reset()`. The text boxes are cleared
  // here as well — a keystroke still inside its debounce window is not in the URL yet,
  // so clearing the URL alone would let the pending write land after the clear.
  const clearFilters = useCallback(() => {
    setQInput("");
    setIpInput("");
    set({
      q: "",
      ip: "",
      since: "",
      until: "",
      role: "all",
      status: "all",
      include_deleted: false,
    });
  }, [set, setQInput, setIpInput]);

  // Only the very first paint freezes the controls: a refresh must stay adjustable
  // (that is what the race guards are for), and a debounced box that goes disabled
  // mid-request loses focus and swallows keystrokes.
  const freezeFilters = useFirstLoad(loading);

  if (detailId) {
    const from = (location.state as { from?: string } | null)?.from ?? "/users";
    return <UserDetail userId={detailId} onBack={() => navigate(from)} />;
  }

  const openUser = (id: string) => {
    navigate(`/users/${id}`, {
      state: { from: `${location.pathname}${location.search}` },
    });
  };

  const filters = (
    <>
      <div className="relative">
        <Search
          size={14}
          aria-hidden
          className="-translate-y-1/2 pointer-events-none absolute top-1/2 left-3 text-muted-foreground"
        />
        <Input
          type="search"
          placeholder="搜索用户名 / 昵称"
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
          disabled={freezeFilters}
          className="w-64 pl-8"
          aria-label="搜索用户名 / 昵称"
        />
      </div>
      <Select
        value={role}
        onChange={(e) => set({ role: e.target.value as UserRole | "all" })}
        disabled={freezeFilters}
        aria-label="按角色筛选"
        options={ROLE_FILTER_OPTIONS}
      />
      <Select
        value={status}
        onChange={(e) => set({ status: e.target.value as UserStatus | "all" })}
        disabled={freezeFilters}
        aria-label="按状态筛选"
        options={STATUS_FILTER_OPTIONS}
      />
      <Input
        type="search"
        placeholder="按 IP 筛选"
        value={ipInput}
        onChange={(e) => setIpInput(e.target.value)}
        disabled={freezeFilters}
        className="w-40"
        aria-label="按 IP 筛选"
        title="匹配注册 IP 或任一登录会话 IP"
      />
      <Input
        type="date"
        value={since}
        onChange={(e) => set({ since: e.target.value })}
        disabled={freezeFilters}
        className="w-36"
        title="注册起始（UTC 日）"
        aria-label="注册起始日期"
      />
      <Input
        type="date"
        value={until}
        onChange={(e) => set({ until: e.target.value })}
        disabled={freezeFilters}
        className="w-36"
        title="注册截止（UTC 日）"
        aria-label="注册截止日期"
      />
      <label className="flex h-9 cursor-pointer select-none items-center gap-2 text-muted-foreground text-sm">
        <input
          type="checkbox"
          checked={includeDeleted}
          onChange={(e) => set({ include_deleted: e.target.checked })}
          disabled={freezeFilters}
          className="size-4 rounded border-input accent-primary disabled:opacity-50"
        />
        显示已注销
      </label>
    </>
  );

  return (
    <Page>
      <PageHeader
        title="用户管理"
        description={`共 ${fmtCount(total, loaded)} 个账号`}
        note="注册日期筛选与「注册时间」列均按 UTC 日切，可能与本地日期相差一天"
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => void load()}
            disabled={loading}
            aria-label="刷新"
          >
            <RefreshCw size={14} className={cn(loading && "animate-spin")} />
          </Button>
        }
        filters={filters}
      />

      {!loaded && loading ? (
        <TableSkeleton columns={COLUMNS} />
      ) : !loading && error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : (
        <Refreshing active={loading}>
          <TableFrame minWidth={1080}>
            {/* 列宽：用户 / 配额 是仅有的两个变长列，其余按内容定宽并禁止换行。
                不封顶时一个长邮箱能把用户列撑到近半屏、把角色与状态挤成竖排断字
                （「活跃」→「活/跃」）——每个单元格自己 nowrap，宽度不再由最长的
                那一行说了算。 */}
            <THead>
              <Th className="w-[240px]">用户</Th>
              <Th className="whitespace-nowrap">角色</Th>
              <Th className="whitespace-nowrap">状态</Th>
              <Th>配额</Th>
              <Th className="whitespace-nowrap">
                <SortHeader
                  label="注册时间"
                  active={sort === "created_at"}
                  order={order}
                  onClick={() => toggleSort("created_at")}
                />
              </Th>
              <Th align="right" className="whitespace-nowrap">
                <SortHeader
                  label="累计成本"
                  active={sort === "cost"}
                  order={order}
                  align="right"
                  onClick={() => toggleSort("cost")}
                />
              </Th>
              <Th align="right" className="whitespace-nowrap">
                操作
              </Th>
            </THead>
            <tbody>
              {users.length === 0 && (
                <TableMessageRow colSpan={COLUMNS}>
                  <EmptyState
                    icon={Users}
                    title="没有匹配的用户"
                    description={
                      filtered
                        ? "当前筛选条件下没有账号，换个条件或清空筛选再试。"
                        : "还没有注册账号。"
                    }
                    action={
                      filtered ? (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={clearFilters}
                        >
                          清空筛选
                        </Button>
                      ) : undefined
                    }
                    className="py-0"
                  />
                </TableMessageRow>
              )}
              {users.map((u) => {
                const isSelf = u.id === selfId;
                const busy = pending.has(u.id);
                // 注销 accounts are anonymized tombstones: surfaced for audit, but
                // their role/quota/status controls are meaningless (and re-enabling a
                // `deleted_<id>` account would be wrong), so the row is read-only.
                const isDeleted = !!u.deleted_at;
                const name = u.display_name || u.username;
                const handle = `@${u.username}${
                  !isDeleted && u.email ? ` · ${u.email}` : ""
                }`;
                return (
                  <TableRow
                    key={u.id}
                    onActivate={isDeleted ? undefined : () => openUser(u.id)}
                    label={isDeleted ? undefined : `查看 ${name} 的用户详情`}
                    className={cn(isDeleted && "opacity-60")}
                  >
                    <Td>
                      <div className="max-w-[240px]">
                        <div className="flex items-baseline gap-2">
                          <span className="truncate font-medium text-foreground">
                            {name}
                          </span>
                          {/* 「不能停用自己」这条守卫的可见理由，长名字也不该把它挤掉 */}
                          {isSelf && (
                            <span className="shrink-0 text-muted-foreground text-xs">
                              (我)
                            </span>
                          )}
                        </div>
                        {/* 邮箱是这一列唯一会失控的部分：截断 + title，完整值在行内
                            下钻的用户详情里。 */}
                        <div
                          className="truncate text-muted-foreground text-xs"
                          title={handle}
                        >
                          {handle}
                        </div>
                      </div>
                    </Td>
                    <Td className="whitespace-nowrap" onClick={stopRowActivation}>
                      {isDeleted ? (
                        <span className="text-muted-foreground text-xs">
                          {u.role}
                        </span>
                      ) : (
                        <Select
                          value={u.role}
                          disabled={isSelf || busy}
                          onChange={(e) =>
                            void patchRow(
                              u,
                              { role: e.target.value as UserRole },
                              "角色已更新",
                            )
                          }
                          title={isSelf ? "不能修改自己的角色" : undefined}
                          aria-label={`${u.username} 的角色`}
                          className="h-8"
                          options={ROLE_OPTIONS}
                        />
                      )}
                    </Td>
                    <Td className="whitespace-nowrap">
                      {isDeleted ? (
                        <Badge tone="neutral">已注销</Badge>
                      ) : u.status === "active" ? (
                        <Badge tone="success">活跃</Badge>
                      ) : (
                        <Badge tone="destructive">已停用</Badge>
                      )}
                    </Td>
                    <Td className="text-muted-foreground text-xs">
                      <div className="max-w-[280px]">
                        {isDeleted ? "—" : quotaSummary(u)}
                      </div>
                    </Td>
                    <Td className="whitespace-nowrap text-muted-foreground text-xs tabular-nums">
                      {fmtDateUtc(u.created_at)}
                    </Td>
                    <Td
                      align="right"
                      className="whitespace-nowrap text-foreground text-xs tabular-nums"
                    >
                      {fmtCny(nanoToYuan(u.cost_total))}
                    </Td>
                    <Td
                      align="right"
                      className="whitespace-nowrap"
                      onClick={stopRowActivation}
                    >
                      {isDeleted ? (
                        <span className="text-muted-foreground text-xs">—</span>
                      ) : (
                        <div className="flex items-center justify-end gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setEditing(u)}
                            disabled={busy}
                          >
                            配额
                          </Button>
                          {u.status === "active" ? (
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={isSelf || busy}
                              title={isSelf ? "不能停用自己" : undefined}
                              className="text-destructive"
                              onClick={() =>
                                void patchRow(
                                  u,
                                  { status: "disabled" },
                                  "账号已停用",
                                )
                              }
                            >
                              {busy ? <Spinner /> : "停用"}
                            </Button>
                          ) : (
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={busy}
                              onClick={() =>
                                void patchRow(
                                  u,
                                  { status: "active" },
                                  "账号已启用",
                                )
                              }
                            >
                              {busy ? <Spinner /> : "启用"}
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-destructive"
                            disabled={isSelf || busy}
                            title={
                              isSelf ? "不能注销自己" : "注销账号（不可恢复）"
                            }
                            onClick={() => setDeleting(u)}
                          >
                            注销
                          </Button>
                        </div>
                      )}
                    </Td>
                  </TableRow>
                );
              })}
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

      {editing && (
        <QuotaDialog
          user={editing}
          onClose={() => setEditing(null)}
          onSaved={(updated) => {
            setUsers((prev) =>
              prev.map((u) => (u.id === updated.id ? { ...u, ...updated } : u)),
            );
            setEditing(null);
          }}
        />
      )}

      {deleting && (
        <DeleteUserDialog
          user={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={onDeleted}
        />
      )}
    </Page>
  );
}

function DeleteUserDialog({
  user,
  onClose,
  onDeleted,
}: {
  user: AdminUser;
  onClose: () => void;
  onDeleted: (updated: AdminUser) => void;
}) {
  const [saving, setSaving] = useState(false);

  const handleDelete = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const updated = await deleteUser(user.id);
      toast.success("账号已注销");
      onDeleted(updated);
    } catch (err) {
      toast.error(errorMessage(err));
      setSaving(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      busy={saving}
      title="注销账号"
      description="不可恢复，也无法重新启用"
      footer={
        <>
          <Button
            variant="outline"
            size="sm"
            onClick={onClose}
            disabled={saving}
          >
            取消
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => void handleDelete()}
            disabled={saving}
          >
            {saving && <Spinner />}
            确认注销
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3 text-muted-foreground text-sm">
        <p>
          将注销{" "}
          <span className="font-medium text-foreground">
            {user.display_name || user.username}
          </span>
          （@{user.username}）。确认后立即生效：
        </p>
        <ul className="flex list-disc flex-col gap-1.5 pl-5">
          <li>
            账号<span className="font-medium text-foreground">匿名化</span>
            ：用户名 / 邮箱 / 头像清除，此后无法登录，也无法恢复。
          </li>
          <li>
            <span className="font-medium text-foreground">
              所有设备立即登出
            </span>
            ，全部登录会话失效。
          </li>
          <li>其对话与分享链接一并清理，BYOK 密钥删除。</li>
          <li>
            账单与审计记录
            <span className="font-medium text-foreground">保留</span>
            ，用于对账与追溯。
          </li>
        </ul>
      </div>
    </Dialog>
  );
}
