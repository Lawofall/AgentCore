import {
  SettingRow,
  SettingsAsync,
  SettingsSection,
  SettingsStack,
} from "@/components/settings";
import { Button, Card, IconButton, PageHeader } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  formatCompact,
  formatCost,
  formatDisplayCost,
  formatQuotaRemaining,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import { useUsageStore } from "@/stores/usage";
import {
  CACHE_BILLED_AS_MISS_LABEL,
  cacheUsageDisplay,
} from "@agentcore/protocol-fold-kit";
import { KeyRound, RefreshCw } from "lucide-react";
import { type ReactNode, useEffect } from "react";
import { Link } from "react-router-dom";

/**
 * Account usage dashboard — 大众面先答「还能用多久」。
 *
 * 平台代付：主卡是额度还剩（日/月取更紧的一窗，不并排两条钱条）；今日花费 /
 * tokens / 请求用数字卡；不限的维不画空槽；近 7 日趋势次之；明细只留构成。
 * BYOK：说明卡 + token 面 + 有产品价目再出 ¥。数字来自 `GET /usage/summary`。
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
  const dayReqUsed = today.requests;
  const moneyCurrency = month.cost.currency ?? today.cost.currency;

  const monthRemaining = remainingNano(monthUsed, monthLimit);
  const dayRemaining = remainingNano(dayCostUsed, dayCostLimit);
  const dayIsHero =
    dayRemaining != null &&
    (monthRemaining == null || dayRemaining < monthRemaining);

  const { dailyResetText, monthlyResetText } = resetTexts();

  const todayStats: StatCell[] = byok
    ? byokTodayStats(summary, {
        dayTokensUsed,
        dayTokenLimit,
        dayReqUsed,
      })
    : [
        {
          label: "花费",
          value: formatCost(dayCostUsed, today.cost.currency),
          near: isNear(dayCostUsed, dayCostLimit) && !dayIsHero,
        },
        {
          label: "tokens",
          value: formatCompact(dayTokensUsed),
          caption:
            dayTokenLimit > 0 ? `/ ${formatCompact(dayTokenLimit)}` : undefined,
          near: isNear(dayTokensUsed, dayTokenLimit),
        },
        {
          label: "请求",
          value: String(dayReqUsed),
          caption: `本月 ${month.requests}`,
        },
      ];

  return (
    <SettingsStack>
      {banner}
      {byok ? (
        <ByokNote />
      ) : (
        <QuotaHero
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
        />
      )}

      <SettingsSection title="今日">
        <TodayStats items={todayStats} />
        {!byok && isNear(dayTokensUsed, dayTokenLimit) && (
          <div className="mt-3">
            <QuotaMeter
              label="今日 tokens"
              used={dayTokensUsed}
              limit={dayTokenLimit}
              caption={`${formatCompact(dayTokensUsed)} / ${formatCompact(dayTokenLimit)} · ${dailyResetText}`}
            />
          </div>
        )}
      </SettingsSection>

      {summary.recent_daily_cost.some((p) => p.cost_total > 0) && !byok && (
        <CostTrend
          points={summary.recent_daily_cost}
          currency={moneyCurrency}
        />
      )}

      <UsageDetail summary={summary} byok={byok} />
    </SettingsStack>
  );
}

function byokTodayStats(
  summary: Summary,
  counts: {
    dayTokensUsed: number;
    dayTokenLimit: number;
    dayReqUsed: number;
  },
): StatCell[] {
  const { today, month } = summary;
  const todayEst = today.estimated_cost?.total ?? 0;
  const items: StatCell[] = [
    {
      label: "tokens",
      value: formatCompact(counts.dayTokensUsed),
      caption:
        counts.dayTokenLimit > 0
          ? `/ ${formatCompact(counts.dayTokenLimit)}`
          : undefined,
    },
    {
      label: "请求",
      value: String(counts.dayReqUsed),
      caption: `本月 ${month.requests}`,
    },
  ];
  if (todayEst > 0) {
    const currency = today.estimated_cost?.currency;
    items.push({
      label: "花费",
      value: formatDisplayCost(
        todayEst,
        (currency || "CNY").toUpperCase() === "USD",
        currency,
      ),
    });
  }
  return items;
}

function QuotaHero({
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
}) {
  if (monthRemaining == null && dayRemaining == null) {
    return (
      <Card className="px-4 py-4">
        <p className="text-sm text-foreground">本月已用</p>
        <p className="mt-1 text-xl font-semibold tabular-nums">
          {formatCost(monthUsed, currency)}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">不限</p>
      </Card>
    );
  }

  const used = dayIsHero ? dayCostUsed : monthUsed;
  const limit = dayIsHero ? dayCostLimit : monthLimit;
  const remaining = dayIsHero ? dayRemaining : monthRemaining;
  const label = dayIsHero ? "今日额度还剩" : "本月额度还剩";
  const pct = usedPct(used, limit);
  const near = isNear(used, limit);
  const resetText = dayIsHero ? dailyResetText : monthlyResetText;

  return (
    <Card className="px-4 py-4">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm text-foreground">{label}</p>
        <p
          className={
            near ? "text-sm text-primary" : "text-sm text-muted-foreground"
          }
        >
          {pct}%
        </p>
      </div>
      <p className="mt-1 text-xl font-semibold tabular-nums">
        {formatQuotaRemaining(remaining ?? 0, currency)}
      </p>
      <QuotaBar label={label} pct={pct} className="mt-3" />
      <p className="mt-1 text-xs text-muted-foreground">
        已用 {formatCost(used, currency)} / {formatCost(limit, currency)} ·{" "}
        {resetText}
      </p>
      {dayIsHero && monthRemaining != null && (
        <p className="mt-1 text-xs text-muted-foreground">
          本月还剩 {formatQuotaRemaining(monthRemaining, currency)} ·{" "}
          {monthlyResetText}
        </p>
      )}
      {near && <NearLimitHint daily={dayIsHero} />}
    </Card>
  );
}

function NearLimitHint({ daily }: { daily: boolean }) {
  return (
    <p className="mt-2 text-xs text-primary">
      {daily ? "接近今日额度" : "接近本月额度"}
      ，用完可联系管理员提额，或
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

type StatCell = {
  label: string;
  value: string;
  caption?: string;
  near?: boolean;
};

function TodayStats({ items }: { items: StatCell[] }) {
  const cols = items.length >= 3 ? "grid-cols-3" : "grid-cols-2";
  return (
    <Card className={cn("grid divide-x divide-border overflow-hidden", cols)}>
      {items.map((item) => (
        <div key={item.label} className="min-w-0 px-4 py-3">
          <p className="text-xs text-muted-foreground">{item.label}</p>
          <p
            className={cn(
              "mt-1 text-base font-medium tabular-nums",
              item.near ? "text-primary" : "text-foreground",
            )}
          >
            {item.value}
          </p>
          {item.caption ? (
            <p className="mt-0.5 text-xs text-muted-foreground">
              {item.caption}
            </p>
          ) : null}
        </div>
      ))}
    </Card>
  );
}

function ByokNote() {
  return (
    <Card variant="muted" className="flex items-start gap-2.5 px-4 py-3">
      <KeyRound size={16} className="mt-0.5 shrink-0 text-muted-foreground" />
      <p className="text-xs text-muted-foreground">
        当前为「自带 Key」模式：对话走你配置的模型与端点，平台不设上限。下方以
        token 为主；有产品价目的模型按同一张人民币账单展示，不扣额度。
      </p>
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
      title="近 7 日成本"
      action={
        <span className="text-xs text-muted-foreground">
          合计 {formatCost(total, currency)}
        </span>
      }
    >
      <table className="sr-only">
        <caption>近 7 日每日成本</caption>
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
            <title>近 7 日成本趋势</title>
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
                  {weekdayLabel(p.date)}
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

  const rows: { label: string; value: string }[] = [
    {
      label: "输入 / 输出",
      value: `输入 ${formatCompact(today.usage.input)} · 输出 ${formatCompact(today.usage.output)}`,
    },
  ];
  if (cache.billedAsMiss) {
    rows.push({
      label: "今日缓存",
      value: `${CACHE_BILLED_AS_MISS_LABEL} · ${formatCompact(cache.cacheMiss)}`,
    });
  } else if (cache.cacheHit > 0) {
    rows.push({
      label: "今日缓存命中率",
      value: `${cache.hitRatePercent ?? 0}%`,
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
        ),
      });
    }
  }

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
