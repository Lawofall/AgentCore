import { CostTrendBars, TurnTrendBars } from "@/components/charts";
import { AuditSummaryWidget } from "@/components/AuditSummaryWidget";
import { GoWindowsCard } from "@/components/GoWindowsCard";
import { CopyableId } from "@/components/CopyableId";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
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
  COST_ESTIMATE_HINT,
  fmtCompact,
  fmtEstimatedMoney,
  fmtInt,
  fmtMoney,
  fmtMs,
  fmtNanoMoney,
  fmtTimeUtc,
  UTC_WINDOW_HINT,
} from "@/lib/utils";
import {
  type AdminObservabilitySummary,
  type TurnHealthWindow,
  type TurnMetricLine,
  fetchObservabilitySummary,
} from "@/services/adminObservability";
import {
  type AdminGoWindows,
  type AdminUsageSummary,
  type ModelCostLine,
  type UsageWindow,
  fetchGoWindows,
  fetchUsageSummary,
} from "@/services/adminUsage";
import { errorMessage } from "@/services/api";
import { CheckCircle2, Coins, Info, RefreshCw } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate, useParams } from "react-router-dom";

/** The two lenses 分析 fuses: 成本 (money) and 健康 (reliability). */
export type AnalyticsSegment = "cost" | "health";

/** A 0..1 fraction as a 1-decimal percentage (e.g. 0.042 → "4.2%"). */
function fmtPct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

/**
 * 分析: the merged platform-wide reporting page (former 用量 + 观测). The two
 * lenses share the same skeleton (window cards + 7-day trend + table + 会话复盘
 * drill-in), so they live behind one segmented control instead of two tabs. Only
 * the active lens fetches, so switching is a fresh load, not a double request.
 */
export function AnalyticsPage() {
  const { segment: segmentParam } = useParams<{ segment: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const [idInput, setIdInput] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [costLoading, setCostLoading] = useState(false);
  const [healthLoading, setHealthLoading] = useState(false);

  if (segmentParam !== "cost" && segmentParam !== "health") {
    return <Navigate to="/analytics/cost" replace />;
  }

  const segment: AnalyticsSegment = segmentParam;

  const openReplay = (conversationId: string) => {
    navigate(`/replay/${conversationId}`, { state: { from: location.pathname } });
  };

  const openUser = (userId: string) => {
    navigate(`/users/${userId}`, { state: { from: location.pathname } });
  };

  const submitReplay = (e: FormEvent) => {
    e.preventDefault();
    const id = idInput.trim();
    if (id) openReplay(id);
  };

  const setSegment = (s: AnalyticsSegment) => navigate(`/analytics/${s}`);

  const activeLoading = segment === "cost" ? costLoading : healthLoading;

  return (
    <Page>
      <PageHeader
        title="分析"
        note={UTC_WINDOW_HINT}
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => setReloadKey((k) => k + 1)}
            disabled={activeLoading}
            aria-label="刷新"
          >
            <RefreshCw size={14} className={cn(activeLoading && "animate-spin")} />
          </Button>
        }
        filters={
          <>
            <SegmentToggle value={segment} onChange={setSegment} />
            <form onSubmit={submitReplay} className="flex items-center gap-2">
              <Input
                value={idInput}
                onChange={(e) => setIdInput(e.target.value)}
                placeholder="会话 ID 复盘…"
                aria-label="按会话 ID 复盘"
                className="w-48"
              />
              <Button
                type="submit"
                variant="outline"
                size="sm"
                disabled={!idInput.trim()}
              >
                复盘
              </Button>
            </form>
          </>
        }
      />

      {segment === "cost" ? (
        <CostPanel
          reloadKey={reloadKey}
          onLoadingChange={setCostLoading}
          onOpenUser={openUser}
        />
      ) : (
        <HealthPanel
          reloadKey={reloadKey}
          onLoadingChange={setHealthLoading}
          onOpenReplay={openReplay}
        />
      )}
    </Page>
  );
}

function SegmentToggle({
  value,
  onChange,
}: {
  value: AnalyticsSegment;
  onChange: (s: AnalyticsSegment) => void;
}) {
  const items: { id: AnalyticsSegment; label: string }[] = [
    { id: "cost", label: "成本" },
    { id: "health", label: "健康" },
  ];
  return (
    <div className="inline-flex items-center rounded-lg border border-border p-0.5">
      {items.map((it) => (
        <button
          key={it.id}
          type="button"
          aria-pressed={value === it.id}
          onClick={() => onChange(it.id)}
          className={cn(
            "h-7 rounded-lg px-3 text-sm font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
            value === it.id
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}

/** First paint of either lens: two window cards, a trend block and a table. */
function PanelSkeleton() {
  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TableSkeleton rows={4} columns={2} />
        <TableSkeleton rows={4} columns={2} />
      </div>
      <TableSkeleton rows={5} columns={7} />
      <TableSkeleton rows={6} columns={4} />
    </div>
  );
}

function CostPanel({
  reloadKey,
  onLoadingChange,
  onOpenUser,
}: {
  reloadKey: number;
  onLoadingChange: (loading: boolean) => void;
  onOpenUser: (userId: string) => void;
}) {
  const [data, setData] = useState<AdminUsageSummary | null>(null);
  const [goWindows, setGoWindows] = useState<AdminGoWindows | null>(null);
  const [goError, setGoError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    onLoadingChange(true);
    setError(null);
    setGoError(null);
    try {
      const [usageResult, goResult] = await Promise.allSettled([
        fetchUsageSummary(),
        fetchGoWindows(),
      ]);
      if (usageResult.status === "fulfilled") {
        setData(usageResult.value);
      } else {
        setData(null);
        setError(errorMessage(usageResult.reason));
      }
      if (goResult.status === "fulfilled") {
        setGoWindows(goResult.value);
      } else {
        setGoWindows(null);
        setGoError(errorMessage(goResult.reason));
      }
    } finally {
      setLoading(false);
      onLoadingChange(false);
    }
  }, [onLoadingChange]);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  if (!data) {
    return loading ? (
      <PanelSkeleton />
    ) : (
      <ErrorState message={error ?? "加载失败"} onRetry={() => void load()} />
    );
  }

  const byok = data.billing_mode === "byok";
  // 行级金额（趋势 / 按模型 / 按用户）不带 currency——同一账本窗口内币种唯一（记账走
  // curated 人民币价卡，BYOK 估算走社区价目快照的美元），且后端明确无汇率换算，所以
  // 符号统一取自窗口 breakdown，绝不按 billing_mode 猜。
  const billedCurrency = data.month.cost.currency;
  const estimatedCurrency =
    data.month.estimated_cost?.currency ??
    data.today.estimated_cost?.currency ??
    null;
  const estimateFmtCurrency = estimatedCurrency ?? billedCurrency;

  return (
    <div className="flex flex-col gap-5">
      {error && <StaleDataNotice message={error} onRetry={() => void load()} />}

      <Refreshing active={loading} className="flex flex-col gap-5">
        {byok && (
          <div className="flex items-start gap-2.5 rounded-xl border border-border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
            <Info size={16} className="mt-0.5 shrink-0 text-primary" />
            <span>
              当前为 <strong className="text-foreground">BYOK（自带 Key）</strong>
              模式：记账成本恒为 0；下方「估算」按社区价目计价
              {estimatedCurrency ? `（${estimatedCurrency}）` : ""}
              ，非上游账单，且平台不做汇率换算。
            </span>
          </div>
        )}

        <GoWindowsCard
          data={goWindows}
          error={goError}
          onRetry={() => void load()}
        />

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <CostWindowCard label="今日" window={data.today} byok={byok} />
          <CostWindowCard label="本月" window={data.month} byok={byok} />
        </div>

        <Card>
          <SectionHeader
            title="近 7 日成本趋势"
            description={`每日记账成本（UTC 日切 · ${billedCurrency}）`}
          />
          <div className="p-5">
            <CostTrendBars
              data={data.recent_daily_cost}
              currency={billedCurrency}
            />
          </div>
        </Card>

        <Card className="overflow-hidden">
          <SectionHeader
            title="本月各模型用量"
            description={`全站按 call 明细聚合（cost_calls · 成本降序）${
              byok ? ` · ${COST_ESTIMATE_HINT}` : ""
            }`}
          />
          <TableFrame minWidth={760} className="rounded-none border-0">
            <THead>
              <Th>模型</Th>
              <Th align="right">调用次数</Th>
              <Th align="right">Tokens</Th>
              <Th align="right">本月成本（{billedCurrency}）</Th>
              <Th align="right">
                估算{estimatedCurrency ? `（${estimatedCurrency}）` : ""}
              </Th>
            </THead>
            <tbody>
              {data.month_by_model.map((row: ModelCostLine) => (
                <TableRow key={row.model}>
                  <Td className="font-medium text-foreground">
                    {row.model || "（未标注）"}
                  </Td>
                  <Td align="right" className="text-muted-foreground tabular-nums">
                    {fmtInt(row.calls)}
                  </Td>
                  <Td align="right" className="text-muted-foreground tabular-nums">
                    {fmtCompact(row.tokens_total)}
                  </Td>
                  <Td
                    align="right"
                    className="font-medium text-foreground tabular-nums"
                  >
                    {fmtNanoMoney(row.cost_total, billedCurrency)}
                  </Td>
                  <Td
                    align="right"
                    className="text-muted-foreground tabular-nums"
                    title={
                      row.cost_estimated_total > 0 ? COST_ESTIMATE_HINT : undefined
                    }
                  >
                    {fmtNanoMoney(
                      row.cost_estimated_total,
                      estimateFmtCurrency,
                      true,
                    )}
                  </Td>
                </TableRow>
              ))}
              {data.month_by_model.length === 0 && (
                <TableMessageRow colSpan={5}>
                  <EmptyState
                    icon={Coins}
                    title="本月暂无模型调用记录"
                    className="py-0"
                  />
                </TableMessageRow>
              )}
            </tbody>
          </TableFrame>
        </Card>

        <Card className="overflow-hidden">
          <SectionHeader
            title="本月 Top 花销用户"
            description="按本月成本降序"
          />
          <TableFrame minWidth={640} className="rounded-none border-0">
            <THead>
              <Th>#</Th>
              <Th>用户</Th>
              <Th align="right">本月成本（{billedCurrency}）</Th>
              <Th align="right">回合数</Th>
            </THead>
            <tbody>
              {data.month_by_user.map((row, i) => (
                <TableRow
                  key={row.user_id}
                  onActivate={() => onOpenUser(row.user_id)}
                  label={`打开用户详情 ${row.display_name || row.username}`}
                >
                  <Td className="text-muted-foreground tabular-nums">{i + 1}</Td>
                  <Td>
                    <div className="font-medium text-foreground">
                      {row.display_name || row.username}
                    </div>
                    <div className="text-muted-foreground text-xs">
                      @{row.username}
                    </div>
                  </Td>
                  <Td align="right" className="font-medium text-foreground tabular-nums">
                    {fmtNanoMoney(row.cost_total, billedCurrency)}
                  </Td>
                  <Td align="right" className="text-muted-foreground tabular-nums">
                    {fmtInt(row.turns)}
                  </Td>
                </TableRow>
              ))}
              {data.month_by_user.length === 0 && (
                <TableMessageRow colSpan={4}>
                  <EmptyState
                    icon={Coins}
                    title="本月暂无花销记录"
                    className="py-0"
                  />
                </TableMessageRow>
              )}
            </tbody>
          </TableFrame>
        </Card>
      </Refreshing>
    </div>
  );
}

function CostWindowCard({
  label,
  window,
  byok,
}: {
  label: string;
  window: UsageWindow;
  byok: boolean;
}) {
  const est = window.estimated_cost;
  // `cny_total` 是后端沿用的旧字段名，实为该 breakdown 自己 `currency` 的主单位。
  return (
    <Card padded>
      <div className="text-muted-foreground text-sm" title={UTC_WINDOW_HINT}>
        {byok ? `${label}估算` : `${label}总成本`}（UTC）
      </div>
      <div
        className="mt-1 text-2xl font-semibold text-foreground tabular-nums"
        title={byok && est ? COST_ESTIMATE_HINT : undefined}
      >
        {byok
          ? fmtEstimatedMoney(est?.cny_total ?? 0, est?.currency)
          : fmtMoney(window.cost.cny_total, window.cost.currency)}
      </div>
      <div className="mt-4 flex items-center gap-6 text-sm">
        <Stat
          label="Token"
          value={fmtCompact(window.usage.input + window.usage.output)}
        />
        <Stat label="请求" value={fmtInt(window.requests)} />
      </div>
    </Card>
  );
}

function HealthPanel({
  reloadKey,
  onLoadingChange,
  onOpenReplay,
}: {
  reloadKey: number;
  onLoadingChange: (loading: boolean) => void;
  onOpenReplay: (conversationId: string) => void;
}) {
  const [data, setData] = useState<AdminObservabilitySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    onLoadingChange(true);
    setError(null);
    try {
      setData(await fetchObservabilitySummary());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
      onLoadingChange(false);
    }
  }, [onLoadingChange]);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  if (!data) {
    return loading ? (
      <PanelSkeleton />
    ) : (
      <ErrorState message={error ?? "加载失败"} onRetry={() => void load()} />
    );
  }

  return (
    <div className="flex flex-col gap-5">
      {error && <StaleDataNotice message={error} onRetry={() => void load()} />}

      <Refreshing active={loading} className="flex flex-col gap-5">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <HealthCard label="今日" window={data.today} />
          <HealthCard label="近 7 日" window={data.week} />
        </div>

        <Card>
          <SectionHeader
            title="近 7 日回合趋势"
            description="每日回合数与其中的错误（UTC 日切）"
          />
          <div className="p-5">
            <TurnTrendBars data={data.recent_daily} />
          </div>
        </Card>

        <AuditSummaryWidget reloadKey={reloadKey} />

        <ErrorsTable rows={data.recent_errors} onOpen={onOpenReplay} />
      </Refreshing>
    </div>
  );
}

function HealthCard({
  label,
  window,
}: {
  label: string;
  window: TurnHealthWindow;
}) {
  // 0 = clean, ≤5% = watch, above = problem — frames the error rate's color.
  const errTone =
    window.errors === 0
      ? "success"
      : window.error_rate <= 0.05
        ? "warning"
        : "destructive";
  // 协作质量 (学·度量 §2.5): the four MAST-labeled signals only mean something once a turn
  // delegated — survival rate is share-of-delegated, the rest are window sums over members.
  const hasDelegated = window.delegated_turns > 0;
  return (
    <Card padded>
      <div className="flex items-baseline justify-between">
        <div className="text-muted-foreground text-sm" title={UTC_WINDOW_HINT}>
          {label}回合数（UTC）
        </div>
        <Badge tone={errTone}>
          错误 {fmtInt(window.errors)} · {fmtPct(window.error_rate)}
        </Badge>
      </div>
      <div className="mt-1 text-2xl font-semibold text-foreground tabular-nums">
        {fmtInt(window.turns)}
      </div>
      <div className="mt-4 grid grid-cols-3 gap-4 text-sm">
        <Stat label="P95 延迟" value={fmtMs(window.p95_duration_ms)} />
        <Stat label="平均轮数" value={window.avg_rounds.toFixed(1)} />
        <Stat label="委派率" value={fmtPct(window.delegated_rate)} />
      </div>
      <div className="mt-4 border-border border-t pt-4">
        <div className="mb-2.5 text-muted-foreground text-xs">
          协作质量 · MAST（委派 {fmtInt(window.delegated_turns)} 回合）
        </div>
        <div className="grid grid-cols-4 gap-4 text-sm">
          <Stat
            label="首计划存活"
            value={hasDelegated ? fmtPct(window.first_plan_survival_rate) : "—"}
            hint="［规格］委派回合中，首个计划未被监督边界（让出 / 改判）打断、一气跑到底的占比 —— 越高越好"
          />
          <Stat
            label="漂移"
            value={hasDelegated ? fmtInt(window.scope_signals) : "—"}
            hint="［错位］worker 越界（scope）升级次数 —— 越低越好"
          />
          <Stat
            label="返工"
            value={hasDelegated ? fmtInt(window.revises) : "—"}
            hint="［验证］队长定向唤回（revise）次数 —— 越低越好"
          />
          <Stat
            label="升级"
            value={hasDelegated ? fmtInt(window.escalations) : "—"}
            hint="worker → 队长 升级信号总数（协作信号）"
          />
        </div>
      </div>
    </Card>
  );
}

function ErrorsTable({
  rows,
  onOpen,
}: {
  rows: TurnMetricLine[];
  onOpen: (conversationId: string) => void;
}) {
  return (
    <Card className="overflow-hidden">
      <SectionHeader title="近期错误" />
      <TableFrame minWidth={860} className="rounded-none border-0">
        <THead>
          <Th title={UTC_WINDOW_HINT}>时间（UTC）</Th>
          <Th>结束原因</Th>
          <Th>错误</Th>
          <Th align="right">耗时</Th>
          <Th>trace</Th>
        </THead>
        <tbody>
          {rows.map((row) => (
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
              <Td>
                {row.trace_id ? (
                  <CopyableId
                    value={row.trace_id}
                    label="trace_id"
                    display={row.trace_id.slice(0, 8)}
                    titleHint={`${row.trace_id}（点击复制 → log_timeline --trace / --pack）`}
                  />
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </Td>
            </TableRow>
          ))}
          {rows.length === 0 && (
            <TableMessageRow colSpan={5}>
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

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div title={hint}>
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-0.5 font-medium text-foreground tabular-nums">{value}</div>
    </div>
  );
}
