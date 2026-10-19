import {
  SettingRow,
  SettingsAsync,
  SettingsSection,
  SettingsStack,
} from "@/components/settings";
import { Badge, Button, Card, IconButton, PageHeader } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  formatCompact,
  formatCost,
  formatDisplayCost,
  formatQuotaRemaining,
} from "@/lib/format";
import { SUBSCRIPTION_PLANS, formatSeatUntil } from "@/lib/subscriptionPlans";
import { cn } from "@/lib/utils";
import { UsageActivity } from "@/pages/more/usage/UsageActivity";
import { useUsageStore } from "@/stores/usage";
import {
  CACHE_BILLED_AS_MISS_LABEL,
  cacheUsageDisplay,
} from "@agentcore/protocol-fold-kit";
import { KeyRound, RefreshCw } from "lucide-react";
import { type ReactNode, useEffect } from "react";
import { Link } from "react-router-dom";

/**
 * Account usage — 大众面先答「还能用多久」。
 *
 * 平台代付：额度行是积分还剩（日/月取更紧的一窗，不并排两条钱条）；搜索次数
 * 和 tokens 将尽并进同一张卡；不限的维不画空槽、不撑仪表盘卡；近 30 日趋势次
 * 之；活动表按对话一行，列头筛模型，汇总跟筛选走同一窗。明细只留缓存计价口
 * 径与 BYOK 月面。BYOK：说明卡 + token 面 + 有产品价目再出 ¥（不扣积分）。数字
 * 来自 `GET /usage/summary`。
 */
export function UsageSettings() {
  const summary = useUsageStore((s) => s.summary);
  const loading = useUsageStore((s) => s.loading);
  const error = useUsageStore((s) => s.error);
  const fetchSummary = useUsageStore((s) => s.fetchSummary);

  useEffect(() => {
    void fetchSummary();
  }, [fetchSummary]);

  const refresh = () => void fetchSummary();
  const byok = summary?.billing_mode === "byok";

  return (
    <div>
      <PageHeader
        title="用量"
        action={
          summary ? (
            <SimpleTooltip label="刷新">
              <IconButton
                size="md"
                aria-label="刷新"
                onClick={refresh}
                disabled={loading}
              >
                <RefreshCw
                  size={16}
                  className={loading ? "animate-spin" : undefined}
                />
              </IconButton>
            </SimpleTooltip>
          ) : undefined
        }
      />

      {summary ? (
        <Dashboard
          summary={summary}
          byok={byok}
          banner={
            error ? (
              <RefreshErrorBanner message={error} onRetry={refresh} />
            ) : null
          }
        />
      ) : (
        <SettingsAsync
          className="mt-6"
          variant="card"
          loading={!error}
          error={error}
          onRetry={refresh}
        />
      )}
    </div>
  );
}

function RefreshErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <Card className="flex items-center justify-between gap-3 border-border bg-muted/40 px-4 py-2.5">
      <p className="text-xs text-muted-foreground">{message}</p>
      <Button variant="neutral" onClick={onRetry}>
        重试
      </Button>
    </Card>
  );
}

type Summary = NonNullable<
  ReturnType<typeof useUsageStore.getState>["summary"]
>;

const NEAR_RATIO = 0.8;

function remainingNano(used: number, limit: number): number | null {
  if (limit <= 0) return null;
  return Math.max(0, limit - used);
}

function usedPct(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return Math.min(Math.round((used / limit) * 100), 100);
}

function isNear(used: number, limit: number): boolean {
  return limit > 0 && used / limit >= NEAR_RATIO;
}

function Dashboard({
  summary,
  byok,
  banner,
}: {
  summary: Summary;
  byok: boolean;
  banner: ReactNode;
}) {
  const { today, month, quota } = summary;
  const monthLimit = quota.monthly_cost_nano;
  const monthUsed = month.cost.total;
  const dayCostLimit = quota.daily_cost_nano;
  const dayCostUsed = today.cost.total;
  const dayTokenLimit = quota.daily_tokens;
  const dayTokensUsed = today.usage.input + today.usage.output;
  const moneyCurrency = month.cost.currency ?? today.cost.currency;

  const monthRemaining = remainingNano(monthUsed, monthLimit);
  const dayRemaining = remainingNano(dayCostUsed, dayCostLimit);
  const dayIsHero =
    dayRemaining != null &&
    (monthRemaining == null || dayRemaining < monthRemaining);

  const { dailyResetText, monthlyResetText } = resetTexts();
  const searchLine = searchCaption(summary.search);
  const tokenNear =
    !byok && isNear(dayTokensUsed, dayTokenLimit)
      ? { used: dayTokensUsed, limit: dayTokenLimit }
      : null;

  return (
    <SettingsStack>
      {banner}
      {byok ? (
        <ByokNote
          searchText={searchLine?.text ?? null}
          searchNear={searchLine?.near ?? false}
        />
      ) : (
        <QuotaRow
          dayIsHero={dayIsHero}
          monthUsed={monthUsed}
          monthLimit={monthLimit}
          monthRemaining={monthRemaining}
          dayCostUsed={dayCostUsed}
          dayCostLimit={dayCostLimit}
          dayRemaining={dayRemaining}
          currency={moneyCurrency}
          dailyResetText={dailyResetText}
          monthlyResetText={monthlyResetText}
          seatPlan={summary.seat.plan}
          seatUntil={summary.seat.until}
          searchText={searchLine?.text ?? null}
          searchNear={searchLine?.near ?? false}
          tokenNear={tokenNear}
        />
      )}

      {summary.recent_daily_cost.some((p) => p.cost_total > 0) && !byok && (
        <CostTrend
          points={summary.recent_daily_cost}
          currency={moneyCurrency}
        />
      )}

      <UsageDetail summary={summary} byok={byok} />

      <UsageActivity models={summary.month_by_model ?? []} />
    </SettingsStack>
  );
}

function searchCaption(
  search: Summary["search"],
): { text: string; near: boolean } | null {
  const dailyCap = search.daily_limit > 0;
  const monthlyCap = search.monthly_limit > 0;
  if (!dailyCap && !monthlyCap) return null;
  if (dailyCap) {
    return {
      text: monthlyCap
        ? `搜索 ${search.daily_used} / ${search.daily_limit} · 本月 ${search.monthly_used}/${search.monthly_limit}`
        : `搜索 ${search.daily_used} / ${search.daily_limit}`,
      near: isNear(search.daily_used, search.daily_limit),
    };
  }
  return {
    text: `搜索 ${search.monthly_used} / ${search.monthly_limit}`,
    near: isNear(search.monthly_used, search.monthly_limit),
  };
}

function QuotaRow({
  dayIsHero,
  monthUsed,
  monthLimit,
  monthRemaining,
  dayCostUsed,
  dayCostLimit,
  dayRemaining,
  currency,
  dailyResetText,
  monthlyResetText,
  seatPlan,
  seatUntil,
  searchText,
  searchNear,
  tokenNear,
}: {
  dayIsHero: boolean;
  monthUsed: number;
  monthLimit: number;
  monthRemaining: number | null;
  dayCostUsed: number;
  dayCostLimit: number;
  dayRemaining: number | null;
  currency?: string | null;
  dailyResetText: string;
  monthlyResetText: string;
  seatPlan: Summary["seat"]["plan"];
  seatUntil: Summary["seat"]["until"];
  searchText: string | null;
  searchNear: boolean;
  tokenNear: { used: number; limit: number } | null;
}) {
  const unlimited = monthRemaining == null && dayRemaining == null;
  const used = dayIsHero ? dayCostUsed : monthUsed;
  const limit = dayIsHero ? dayCostLimit : monthLimit;
  const remaining = dayIsHero ? dayRemaining : monthRemaining;
  const label = unlimited
    ? "本月已用"
    : dayIsHero
      ? "今日积分还剩"
      : "本月积分还剩";
  const pct = unlimited ? 0 : usedPct(used, limit);
  const near = !unlimited && isNear(used, limit);
  const resetText = dayIsHero ? dailyResetText : monthlyResetText;
  const until = seatUntil ? ` · ${formatSeatUntil(seatUntil)}到期` : "";
  const value = unlimited
    ? `${formatCost(monthUsed, currency)} · 不限${until}`
    : formatQuotaRemaining(remaining ?? 0, currency);
  const extras = (
    <QuotaExtras
      searchText={searchText}
      searchNear={searchNear}
      tokenNear={tokenNear}
      dailyResetText={dailyResetText}
    />
  );
  const hasBody = !unlimited || Boolean(searchText || tokenNear);

  return (
    <Card>
      <SettingRow
        surface="list"
        label={label}
        value={value}
        control={<SeatChip plan={seatPlan} />}
      />
      {hasBody ? (
        <div className="space-y-2 px-4 pb-3">
          {unlimited ? null : (
            <>
              <QuotaBar label={label} pct={pct} />
              <p className="text-xs text-muted-foreground">
                已用 {formatCost(used, currency)} /{" "}
                {formatCost(limit, currency)} ·{" "}
                <span
                  className={near ? "text-primary" : "text-muted-foreground"}
                >
                  {pct}%
                </span>
                {" · "}
                {resetText}
                {until}
              </p>
              {dayIsHero && monthRemaining != null ? (
                <p className="text-xs text-muted-foreground">
                  本月还剩 {formatQuotaRemaining(monthRemaining, currency)} ·{" "}
                  {monthlyResetText}
                </p>
              ) : null}
              {near ? (
                <NearLimitHint
                  daily={dayIsHero}
                  canUpgrade={seatPlan !== "flagship"}
                />
              ) : null}
            </>
          )}
          {extras}
        </div>
      ) : null}
    </Card>
  );
}

function QuotaExtras({
  searchText,
  searchNear,
  tokenNear,
  dailyResetText,
}: {
  searchText: string | null;
  searchNear: boolean;
  tokenNear: { used: number; limit: number } | null;
  dailyResetText: string;
}) {
  return (
    <>
      {searchText ? (
        <p
          className={
            searchNear
              ? "text-xs text-primary"
              : "text-xs text-muted-foreground"
          }
        >
          {searchText}
        </p>
      ) : null}
      {tokenNear ? (
        <QuotaMeter
          label="今日 tokens"
          used={tokenNear.used}
          limit={tokenNear.limit}
          caption={`${formatCompact(tokenNear.used)} / ${formatCompact(tokenNear.limit)} · ${dailyResetText}`}
        />
      ) : null}
    </>
  );
}

function SeatChip({ plan }: { plan: Summary["seat"]["plan"] }) {
  return (
    <Link to="/more/subscription" className="shrink-0" aria-label="当前席位">
      <Badge pill>{SUBSCRIPTION_PLANS[plan].name}</Badge>
    </Link>
  );
}

function NearLimitHint({
  daily,
  canUpgrade,
}: {
  daily: boolean;
  canUpgrade: boolean;
}) {
  const window = daily ? "接近今日积分" : "接近本月积分";
  if (canUpgrade) {
    return (
      <p className="text-xs text-primary">
        {window}，用完可
        <Link
          to="/more/subscription"
          className="underline-offset-2 hover:underline"
        >
          去订阅
        </Link>
        开通更高档，或
        <Link
          to="/more/providers"
          className="underline-offset-2 hover:underline"
        >
          接入自己的 Key
        </Link>
        继续。
      </p>
    );
  }
  return (
    <p className="text-xs text-primary">
      {window}，用完后额度重置可继续，或
      <Link to="/more/providers" className="underline-offset-2 hover:underline">
        接入自己的 Key
      </Link>
      继续。
    </p>
  );
}

function QuotaBar({
  label,
  pct,
  className,
}: {
  label: string;
  pct: number;
  className?: string;
}) {
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      className={cn(
        "h-2 w-full overflow-hidden rounded-full bg-muted",
        className,
      )}
    >
      <div
        className="h-full rounded-full bg-primary"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function ByokNote({
  searchText,
  searchNear,
}: {
  searchText: string | null;
  searchNear: boolean;
}) {
  return (
    <Card variant="muted" className="flex items-start gap-2.5 px-4 py-3">
      <KeyRound size={16} className="mt-0.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0">
        <p className="text-xs text-muted-foreground">
          当前为「自带 Key」模式：对话走你配置的模型与端点，平台不设上限。下方以
          token 为主；有产品价目的模型按官网价写成人民币，不扣积分。
        </p>
        {searchText ? (
          <p
            className={
              searchNear
                ? "mt-2 text-xs text-primary"
                : "mt-2 text-xs text-muted-foreground"
            }
          >
            {searchText}
          </p>
        ) : null}
      </div>
    </Card>
  );
}

function resetTexts(): { dailyResetText: string; monthlyResetText: string } {
  const now = new Date();
  const dailyReset = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1),
  );
  const monthlyReset = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1),
  );
  const pad = (n: number) => String(n).padStart(2, "0");
  const hhmm = `${pad(dailyReset.getHours())}:${pad(dailyReset.getMinutes())}`;
  return {
    dailyResetText: `每日 ${hhmm} 重置`,
    monthlyResetText: `${monthlyReset.getMonth() + 1} 月 ${monthlyReset.getDate()} 日 ${hhmm} 重置`,
  };
}

function QuotaMeter({
  label,
  used,
  limit,
  caption,
}: {
  label: string;
  used: number;
  limit: number;
  caption: string;
}) {
  const pct = usedPct(used, limit);
  const near = isNear(used, limit);

  return (
    <div>
      <div className="flex items-center justify-between text-sm">
        <span className="text-foreground">{label}</span>
        <span className={near ? "text-primary" : "text-muted-foreground"}>
          {pct}%
        </span>
      </div>
      <QuotaBar label={label} pct={pct} className="mt-1.5" />
      <p className="mt-1 text-xs text-muted-foreground">{caption}</p>
    </div>
  );
}

function weekdayLabel(isoDate: string): string {
  const d = new Date(`${isoDate}T00:00:00Z`);
  return `周${["日", "一", "二", "三", "四", "五", "六"][d.getUTCDay()]}`;
}

/** Headroom so the max-day dot isn't clipped at the plot top. */
const TREND_TOP_PAD = 8;

function trendY(cost: number, max: number): number {
  if (max <= 0) return 100;
  return TREND_TOP_PAD + (1 - cost / max) * (100 - TREND_TOP_PAD);
}

function trendCoords(
  points: Summary["recent_daily_cost"],
  max: number,
): { x: number; y: number }[] {
  const n = points.length;
  if (n === 0) return [];
  return points.map((p, i) => ({
    x: ((i + 0.5) / n) * 100,
    y: trendY(p.cost_total, max),
  }));
}

function trendLineD(coords: { x: number; y: number }[]): string {
  return coords
    .map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(2)} ${c.y.toFixed(2)}`)
    .join(" ");
}

function trendAreaD(coords: { x: number; y: number }[]): string {
  const first = coords[0];
  const last = coords[coords.length - 1];
  if (!first || !last) return "";
  const ridge = coords
    .map((c) => `L${c.x.toFixed(2)} ${c.y.toFixed(2)}`)
    .join(" ");
  return `M${first.x.toFixed(2)} 100 ${ridge} L${last.x.toFixed(2)} 100 Z`;
}

function CostTrend({
  points,
  currency,
}: {
  points: Summary["recent_daily_cost"];
  currency?: string | null;
}) {
  // 折线 + 锚点画趋势；零日落基线（不画地板柱）。图是装饰，数字在 sr-only 表。
  const max = points.reduce((m, p) => Math.max(m, p.cost_total), 0);
  const total = points.reduce((s, p) => s + p.cost_total, 0);
  const coords = trendCoords(points, max);
  const lineD = trendLineD(coords);
  const areaD = trendAreaD(coords);

  return (
    <SettingsSection
      title="近 30 日成本"
      action={
        <span className="text-xs text-muted-foreground">
          合计 {formatCost(total, currency)}
        </span>
      }
    >
      <table className="sr-only">
        <caption>近 30 日每日成本</caption>
        <thead>
          <tr>
            <th scope="col">日期</th>
            <th scope="col">成本</th>
          </tr>
        </thead>
        <tbody>
          {points.map((p) => (
            <tr key={p.date}>
              <th scope="row">{p.date}</th>
              <td>{formatCost(p.cost_total, currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="relative">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-28 border-b border-border">
          <svg
            aria-hidden
            className="size-full overflow-visible"
            preserveAspectRatio="none"
            viewBox="0 0 100 100"
          >
            <title>近 30 日成本趋势</title>
            {areaD ? <path className="fill-primary/15" d={areaD} /> : null}
            {lineD ? (
              <path
                className="fill-none stroke-primary"
                d={lineD}
                data-testid="cost-trend-line"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                vectorEffect="non-scaling-stroke"
              />
            ) : null}
          </svg>
        </div>
        <div className="relative flex" aria-hidden>
          {points.map((p, i) => (
            <SimpleTooltip
              key={p.date}
              label={`${weekdayLabel(p.date)} · ${formatCost(p.cost_total, currency)}`}
            >
              <div className="flex min-w-0 flex-1 flex-col">
                <div className="relative h-28 w-full">
                  <div
                    className="absolute left-1/2 size-2 -translate-x-1/2 translate-y-1/2 rounded-full bg-primary ring-2 ring-background"
                    data-testid="cost-trend-dot"
                    style={{ bottom: `${100 - (coords[i]?.y ?? 100)}%` }}
                  />
                </div>
                <span className="mt-2 text-center text-xs text-muted-foreground">
                  {i === 0 || i === points.length - 1 || i % 7 === 0
                    ? weekdayLabel(p.date)
                    : ""}
                </span>
              </div>
            </SimpleTooltip>
          ))}
        </div>
      </div>
    </SettingsSection>
  );
}

function UsageDetail({
  summary,
  byok,
}: {
  summary: Summary;
  byok: boolean;
}) {
  const { today, month } = summary;
  const cache = cacheUsageDisplay(today.usage);
  const monthEst = month.estimated_cost?.total ?? 0;

  const rows: { label: string; value: string }[] = [];
  if (cache.billedAsMiss) {
    rows.push({
      label: "今日缓存",
      value: `${CACHE_BILLED_AS_MISS_LABEL} · ${formatCompact(cache.cacheMiss)}`,
    });
  }
  if (byok) {
    rows.push({
      label: "本月 tokens",
      value: `输入 ${formatCompact(month.usage.input)} · 输出 ${formatCompact(month.usage.output)}`,
    });
    if (monthEst > 0) {
      const currency = month.estimated_cost?.currency;
      rows.push({
        label: "本月花费",
        value: formatDisplayCost(
          monthEst,
          (currency || "CNY").toUpperCase() === "USD",
          currency,
          "cash",
        ),
      });
    }
  }

  if (rows.length === 0) return null;

  return (
    <Card>
      {rows.map((row, i) => (
        <SettingRow
          key={row.label}
          surface="list"
          divider={i > 0}
          label={row.label}
          value={row.value}
        />
      ))}
    </Card>
  );
}
