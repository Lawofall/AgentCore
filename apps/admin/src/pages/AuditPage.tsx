import { CopyableId } from "@/components/CopyableId";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Card, Page, PageHeader } from "@/components/ui/Page";
import { Pagination } from "@/components/ui/Pagination";
import { Select, type SelectOption } from "@/components/ui/Select";
import {
  EmptyState,
  ErrorState,
  Refreshing,
  TableSkeleton,
} from "@/components/ui/States";
import { TableFrame, TableRow, THead, Td, Th } from "@/components/ui/Table";
import { useAdminListPage } from "@/hooks/useAdminListPage";
import { useFirstLoad } from "@/hooks/useFirstLoad";
import { oneOf, str, useUrlFilters } from "@/hooks/useUrlFilters";
import { cn, fmtTime } from "@/lib/utils";
import {
  AUDIT_ACTION_LABELS,
  type AdminAuditLogLine,
  listAuditLogs,
} from "@/services/adminAudit";
import { type AdminUserListItem, listUsers } from "@/services/adminUsers";
import { errorMessage } from "@/services/api";
import { Eye, History, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

const PAGE_SIZE = 50;

const ACTION_FILTERS: SelectOption[] = [
  { value: "", label: "全部操作" },
  { value: "user.update", label: "修改用户" },
  { value: "user.reset_password", label: "重置密码" },
  { value: "user.set_password", label: "设置密码" },
  { value: "account.change_password", label: "修改密码" },
  { value: "user.delete", label: "注销账号" },
  { value: "conversation.replay", label: "回放对话" },
];

/**
 * Param names are the API's own query fields (`action`, `actor_id`). The action codec
 * reads the dropdown itself, so a hand-edited `?action=` can never carry a value the
 * page cannot also offer as a way back out.
 */
const AUDIT_FILTERS = {
  action: oneOf(
    ACTION_FILTERS.map((o) => o.value),
    "",
  ),
  actor_id: str(),
};

type DetailEntry = [string, unknown];

function detailEntries(detail: AdminAuditLogLine["detail"]): DetailEntry[] {
  return detail ? Object.entries(detail) : [];
}

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function previewValue(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return `[${value.length}]`;
  if (typeof value === "object") return "{…}";
  return clip(String(value), 24);
}

/**
 * One line of gist for the 详情 column.
 *
 * The column used to hold the whole `JSON.stringify(detail)` behind `truncate` +
 * `title`, so anything past ~40 characters was readable only as a hover tooltip —
 * unreachable by keyboard and impossible to copy. Now the cell shows the first
 * couple of fields and the full document opens in a dialog.
 */
function detailPreview(entries: DetailEntry[]): string {
  const head = entries
    .slice(0, 2)
    .map(([key, value]) => `${key}=${previewValue(value)}`)
    .join(" · ");
  const rest = entries.length - 2;
  return rest > 0 ? `${head} +${rest}` : head;
}

function AuditTarget({ row }: { row: AdminAuditLogLine }) {
  const location = useLocation();
  const from = `${location.pathname}${location.search}`;

  if (!row.target_id) {
    return <span className="text-muted-foreground">—</span>;
  }

  const label = `${row.target_type} · ${row.target_id.slice(0, 8)}…`;

  if (row.target_type === "user") {
    return (
      <Link
        to={`/users/${row.target_id}`}
        state={{ from }}
        className="font-mono text-xs text-primary underline-offset-2 hover:underline"
        title={row.target_id}
      >
        {label}
      </Link>
    );
  }

  if (row.target_type === "conversation") {
    return (
      <Link
        to={`/replay/${row.target_id}`}
        state={{ from }}
        className="font-mono text-xs text-primary underline-offset-2 hover:underline"
        title={row.target_id}
      >
        {label}
      </Link>
    );
  }

  return (
    <span className="font-mono text-xs text-muted-foreground" title={row.target_id}>
      {label}
    </span>
  );
}

export function AuditPage() {
  const [rows, setRows] = useState<AdminAuditLogLine[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useAdminListPage();
  const { values, set, reset } = useUrlFilters(AUDIT_FILTERS);
  const { action, actor_id: actorId } = values;
  /**
   * Name of an actor picked from a row, who may not be in the admin roster. Kept
   * alongside the id it describes: `actor_id` can now change without going through
   * that button (a Back step, a pasted link), and a bare name would then label the
   * wrong operator.
   */
  const [actorHint, setActorHint] = useState<{ id: string; name: string } | null>(
    null,
  );
  const [operators, setOperators] = useState<AdminUserListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detailRow, setDetailRow] = useState<AdminAuditLogLine | null>(null);
  // 操作者 / 操作类型 flips and page changes can overlap; only the latest response wins.
  const loadGenRef = useRef(0);
  const loadAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    void listUsers({ page: 1, pageSize: 100, role: "admin" })
      .then((res) => setOperators(res.data))
      .catch(() => {
        /* actor filter degrades to “全部” if roster fails */
      });
  }, []);

  const load = useCallback(async () => {
    loadAbortRef.current?.abort();
    const ac = new AbortController();
    loadAbortRef.current = ac;
    const gen = ++loadGenRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await listAuditLogs(
        {
          page,
          pageSize: PAGE_SIZE,
          action: action || undefined,
          actorId: actorId || undefined,
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
  }, [page, action, actorId]);

  useEffect(() => {
    void load();
    return () => {
      loadAbortRef.current?.abort();
    };
  }, [load]);

  const actorOptions = useMemo<SelectOption[]>(() => {
    const options: SelectOption[] = [{ value: "", label: "全部操作者" }];
    for (const op of operators) {
      options.push({ value: op.id, label: op.display_name || op.username });
    }
    // An actor picked from a row — or restored from a shared link — may have been
    // demoted since; without this the select would render blank while the filter is
    // very much active.
    if (actorId && !operators.some((op) => op.id === actorId)) {
      options.push({
        value: actorId,
        label:
          actorHint?.id === actorId ? actorHint.name : `${actorId.slice(0, 8)}…`,
      });
    }
    return options;
  }, [operators, actorId, actorHint]);

  const filtered = Boolean(action || actorId);
  const firstLoad = loading && rows.length === 0 && !error;
  const freezeFilters = useFirstLoad(loading);
  const outOfRange = rows.length === 0 && total > 0 && page > 1;

  const filterByActor = (row: AdminAuditLogLine) => {
    setActorHint({ id: row.actor_id, name: row.actor_username });
    set({ actor_id: row.actor_id });
  };

  return (
    <Page>
      <PageHeader
        title="操作审计"
        note="时间为本机时区，格式 MM-DD HH:mm"
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
        filters={
          <>
            <Select
              aria-label="按操作者筛选"
              value={actorId}
              disabled={freezeFilters}
              onChange={(e) => set({ actor_id: e.target.value })}
              options={actorOptions}
              className="max-w-[220px]"
            />
            <Select
              aria-label="按操作类型筛选"
              value={action}
              disabled={freezeFilters}
              onChange={(e) => set({ action: e.target.value })}
              options={ACTION_FILTERS}
            />
            {filtered && (
              <Button
                variant="ghost"
                size="sm"
                onClick={reset}
                disabled={freezeFilters}
              >
                清除筛选
              </Button>
            )}
          </>
        }
      />

      {firstLoad ? (
        <TableSkeleton columns={5} />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : (
        <Refreshing active={loading}>
          {rows.length === 0 ? (
            <Card>
              {outOfRange ? (
                // 共 N 条 next to “暂无记录” reads as data loss; it's just a stale
                // `?page=` from a bookmark or a back step.
                <EmptyState
                  icon={History}
                  title="这一页没有审计记录"
                  description={`当前共 ${total} 条，第 ${page} 页已超出范围。`}
                  action={
                    <Button variant="outline" size="sm" onClick={() => setPage(1)}>
                      回到第一页
                    </Button>
                  }
                />
              ) : (
                <EmptyState
                  icon={History}
                  title={filtered ? "没有符合筛选的审计记录" : "暂无审计记录"}
                  description={
                    filtered
                      ? "换个操作者或操作类型再看，或清除筛选查看全部。"
                      : "管理员执行特权操作（改用户、重置密码、回放对话等）后会记在这里。"
                  }
                  action={
                    filtered ? (
                      <Button variant="outline" size="sm" onClick={reset}>
                        清除筛选
                      </Button>
                    ) : undefined
                  }
                />
              )}
            </Card>
          ) : (
            <TableFrame minWidth={880}>
              <THead>
                <Th>时间</Th>
                <Th>操作者</Th>
                <Th>操作</Th>
                <Th>目标</Th>
                <Th>详情</Th>
              </THead>
              <tbody>
                {rows.map((row) => {
                  const entries = detailEntries(row.detail);
                  return (
                    <TableRow key={row.id}>
                      <Td className="whitespace-nowrap tabular-nums text-muted-foreground">
                        {fmtTime(row.created_at)}
                      </Td>
                      <Td>
                        <button
                          type="button"
                          className="rounded text-foreground underline-offset-2 outline-none hover:text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                          onClick={() => filterByActor(row)}
                          title="按此操作者筛选"
                        >
                          {row.actor_username}
                        </button>
                      </Td>
                      <Td>
                        <Badge tone="neutral">
                          {AUDIT_ACTION_LABELS[row.action] ?? row.action}
                        </Badge>
                      </Td>
                      <Td>
                        <AuditTarget row={row} />
                      </Td>
                      <Td>
                        {entries.length === 0 ? (
                          <span className="text-muted-foreground">—</span>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="-ml-3 max-w-[320px] font-mono text-xs font-normal text-muted-foreground"
                            onClick={() => setDetailRow(row)}
                            aria-label={`查看详情：${row.actor_username} ${
                              AUDIT_ACTION_LABELS[row.action] ?? row.action
                            }`}
                          >
                            <Eye size={12} className="shrink-0" />
                            <span className="truncate">{detailPreview(entries)}</span>
                          </Button>
                        )}
                      </Td>
                    </TableRow>
                  );
                })}
              </tbody>
            </TableFrame>
          )}
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={total}
            onPageChange={setPage}
            disabled={loading}
          />
        </Refreshing>
      )}

      {detailRow && (
        <Dialog
          open
          onClose={() => setDetailRow(null)}
          title="审计详情"
          description={`${AUDIT_ACTION_LABELS[detailRow.action] ?? detailRow.action} · ${detailRow.actor_username} · ${fmtTime(detailRow.created_at)}`}
          size="lg"
          footer={
            <Button variant="outline" size="sm" onClick={() => setDetailRow(null)}>
              关闭
            </Button>
          }
        >
          {detailRow.target_id && (
            <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">目标</span>
              <span className="text-foreground">{detailRow.target_type}</span>
              <CopyableId value={detailRow.target_id} label="target_id" />
            </div>
          )}
          <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap break-all rounded-lg border border-border bg-muted/40 p-3 font-mono text-xs text-foreground">
            {JSON.stringify(detailRow.detail, null, 2)}
          </pre>
        </Dialog>
      )}
    </Page>
  );
}
