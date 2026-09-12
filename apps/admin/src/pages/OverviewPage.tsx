import { DeploySnapshot } from "@/components/DeploySnapshot";
import { TurnTrendBars } from "@/components/charts";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, Page, PageHeader, SectionHeader } from "@/components/ui/Page";
import {
  EmptyState,
  ErrorState,
  Refreshing,
  StaleDataNotice,
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
import {
  cn,
  fmtCompact,
  fmtInt,
  fmtMoney,
  fmtMs,
  fmtTimeUtc,
  UTC_WINDOW_HINT,
} from "@/lib/utils";
import type { TurnMetricLine } from "@/services/adminObservability";
import { type AdminOverview, fetchOverview } from "@/services/adminOverview";
import { errorMessage } from "@/services/api";
import { CheckCircle2, ChevronRight, RefreshCw } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

/** A 0..1 fraction as a 1-decimal percentage (e.g. 0.042 → "4.2%"). */
function fmtPct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

/** Recent errors preview shows only the freshest few — the full feed lives on 对话（仅有错误）. */
const ERROR_PREVIEW_LIMIT = 5;

/**
 * 总览: today's pulse + deploy snapshot + the 7-day turn trend + recent errors.
 * Cost trend and credential pool live on 供给; conversation index on 对话.
 */
export function OverviewPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [data, setData] = useState<AdminOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deployReloadKey, setDeployReloadKey] = useState(0);

  const openReplay = (conversationId: string) => {
    navigate(`/replay/${conversationId}`, { state: { from: location.pathname } });
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchOverview());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const refresh = useCallback(() => {
    setDeployReloadKey((k) => k + 1);
    void load();
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  const today = data?.today;
  const errTone =
    !today || today.errors === 0
      ? "success"
      : today.error_rate <= 0.05
        ? "warning"
        : "destructive";
  const byok = data?.billing_mode === "byok";

  return (
    <Page>
      <PageHeader
        title="总览"
        note={UTC_WINDOW_HINT}
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={refresh}
            disabled={loading}
            aria-label="刷新"
          >
            <RefreshCw size={14} className={cn(loading && "animate-spin")} />
          </Button>
        }
      />

      {!data && loading && <OverviewSkeleton />}

      {!data && !loading && error && (
        <ErrorState message={error} onRetry={() => void load()} />
      )}

      {data && today && (
        <div className="flex flex-col gap-5">
          {error && (
            <StaleDataNotice message={error} onRetry={() => void load()} />
          )}

          <Refreshing active={loading} className="flex flex-col gap-5">
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-5">
              <MetricCard
                label="今日活跃用户"
                value={fmtInt(data.active_users_today)}
                sub={`共 ${fmtInt(data.users_active)} 活跃账号`}
                onClick={() => navigate("/users")}
              />
              <MetricCard
                label="今日回合"
                value={fmtInt(today.turns)}
                badge={
                  <Badge tone={errTone}>
                    错误 {fmtInt(today.errors)} · {fmtPct(today.error_rate)}
                  </Badge>
                }
                onClick={() =>
                  navigate(
                    today.errors > 0
                      ? "/conversations?has_errors=yes"
                      : "/conversations",
                  )
                }
              />
              <MetricCard
                label="今日成本"
                value={fmtMoney(data.cost_today.cny_total, data.cost_today.currency)}
                sub={byok ? "BYOK 记账恒为 0，估算见供给·成本" : undefined}
                onClick={() => navigate("/analytics/cost")}
              />
              <MetricCard
                label="今日 Token"
                value={fmtCompact(today.input_tokens + today.output_tokens)}
                sub={`输入 ${fmtCompact(today.input_tokens)} · 输出 ${fmtCompact(today.output_tokens)}`}
                onClick={() => navigate("/analytics/cost")}
              />
              <MetricCard
                label="P95 延迟"
                value={fmtMs(today.p95_duration_ms)}
                sub={`平均 ${today.avg_rounds.toFixed(1)} 轮 · 委派 ${fmtPct(today.delegated_rate)}`}
              />
            </div>

            <div className="flex flex-wrap items-center gap-x-1 gap-y-1 rounded-xl border border-border bg-card px-3 py-2 text-sm">
              <button
                type="button"
                onClick={() => navigate("/quota")}
                className="flex items-center gap-2 rounded-lg px-2 py-2 text-left outline-none transition-colors hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="text-muted-foreground">计费模式</span>
                <Badge tone="primary">{byok ? "BYOK · 自带 Key" : "平台付费"}</Badge>
                <span className="inline-flex items-center gap-0.5 text-muted-foreground text-xs">
                  供给 · 额度
                  <ChevronRight size={14} />
                </span>
              </button>
            </div>

            <DeploySnapshot reloadKey={deployReloadKey} />

            <Card>
              <SectionHeader
                title="近 7 日回合趋势"
                description="每日回合数与其中的错误（UTC 日切）"
              />
              <div className="p-5">
                <TurnTrendBars data={data.recent_daily_turns} />
              </div>
            </Card>

            <ErrorsPreview
              rows={data.recent_errors}
              onOpen={openReplay}
              onViewAll={() => navigate("/conversations?has_errors=yes")}
            />
          </Refreshing>
        </div>
      )}
    </Page>
  );
}

/** First paint keeps the page's shape (tiles → deploy → trend → errors) instead of a spinner. */
function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 5 }, (_, i) => (
          <TableSkeleton key={`tile-${i}`} rows={2} columns={1} />
        ))}
      </div>
      <TableSkeleton rows={1} columns={3} />
      <TableSkeleton rows={5} columns={3} />
      <TableSkeleton rows={5} columns={3} />
      <TableSkeleton rows={5} columns={4} />
    </div>
  );
}

function DetailLink({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-0.5 rounded text-muted-foreground text-xs outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
    >
      {label}
      <ChevronRight size={14} />
    </button>
  );
}

/**
 * A summary tile. Stays a `<button>` rather than a `Card`: the whole tile is the
 * link into the page that owns the detail, so it has to be focusable and activatable.
 */
function MetricCard({
  label,
  value,
  sub,
  badge,
  onClick,
}: {
  label: string;
  value: string;
  sub?: string;
  badge?: ReactNode;
  onClick?: () => void;
}) {
  const inner = (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground text-sm">{label}</span>
        {badge}
      </div>
      <div className="mt-1 text-2xl font-semibold text-foreground tabular-nums">
        {value}
      </div>
      {sub && <div className="mt-2 text-muted-foreground text-xs">{sub}</div>}
    </>
  );
  const className =
    "rounded-xl border border-border bg-card p-5 text-left outline-none";
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={cn(
          className,
          "transition-colors hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        {inner}
      </button>
    );
  }
  return <div className={className}>{inner}</div>;
}

function ErrorsPreview({
  rows,
  onOpen,
  onViewAll,
}: {
  rows: TurnMetricLine[];
  onOpen: (conversationId: string) => void;
  onViewAll: () => void;
}) {
  const preview = rows.slice(0, ERROR_PREVIEW_LIMIT);
  return (
    <Card className="overflow-hidden">
      <SectionHeader
        title="近期错误"
        action={<DetailLink label="查看全部" onClick={onViewAll} />}
      />
      <TableFrame minWidth={640} className="rounded-none border-0">
        <THead>
          <Th title={UTC_WINDOW_HINT}>时间（UTC）</Th>
          <Th>结束原因</Th>
          <Th>错误</Th>
          <Th align="right">耗时</Th>
        </THead>
        <tbody>
          {preview.map((row) => (
            <TableRow
              key={row.turn_id}
              onActivate={() => onOpen(row.conversation_id)}
              label={`打开会话复盘 ${row.conversation_id}`}
              className="align-top"
            >
              <Td className="whitespace-nowrap text-muted-foreground tabular-nums">
                {fmtTimeUtc(row.created_at)}
              </Td>
              <Td>
                <Badge tone="destructive">{row.finish_reason ?? "error"}</Badge>
              </Td>
              <Td className="max-w-md text-foreground">
                <span className="line-clamp-2 break-words">{row.error ?? "—"}</span>
              </Td>
              <Td
                align="right"
                className="whitespace-nowrap text-muted-foreground tabular-nums"
              >
                {fmtMs(row.duration_ms)}
              </Td>
            </TableRow>
          ))}
          {preview.length === 0 && (
            <TableMessageRow colSpan={4}>
              <EmptyState
                icon={CheckCircle2}
                title="近期暂无错误回合"
                className="py-0"
              />
            </TableMessageRow>
          )}
        </tbody>
      </TableFrame>
    </Card>
  );
}
