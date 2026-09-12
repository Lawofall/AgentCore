import { EmptyState } from "@/components/ui/States";
import {
  cn,
  currencySymbol,
  fmtInt,
  fmtMoney,
  mmdd,
  nanoToMajor,
  UTC_WINDOW_HINT,
} from "@/lib/utils";
import type { DailyTurns } from "@/services/adminObservability";
import type { DailyCost } from "@/services/adminUsage";
import { BarChart3 } from "lucide-react";
import type { ReactNode } from "react";

/**
 * Shared 7-day trend bars for the admin console (供给·成本 / 用户详情 show the
 * cost sparkline; 总览 shows the turn one). Kept presentational and
 * dependency-light so every surface renders an identical chart instead of drifting.
 *
 * Three rules the reading of these bars depends on:
 * - **A zero day gets no bar.** A floor-height stub on an empty day reads as "there
 *   was spend every day" — the exact misread under BYOK, where billed spend is 0 all
 *   week. Only a real-but-tiny value keeps the 2% visibility floor.
 * - **Days are UTC days.** `date` comes off the backend's UTC bucketing; the axis is
 *   labeled as such and never re-zoned (see `UTC_WINDOW_HINT`).
 * - **Every number is readable without the chart.** The bars are decorative markup
 *   whose only value carrier used to be a hover `title`; the figures now live in a
 *   real (visually hidden) table, so keyboard and screen-reader users get the data
 *   rather than an unlabelled box.
 */

/** 每日数值行：柱形图的等价可读表格（`sr-only`，供读屏 / 键盘路径）。 */
function TrendTable({
  caption,
  valueHeader,
  rows,
}: {
  caption: string;
  valueHeader: string;
  rows: { date: string; value: string }[];
}) {
  return (
    <table className="sr-only" data-testid="trend-table">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">日期（UTC）</th>
          <th scope="col">{valueHeader}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.date}>
            <th scope="row">{r.date}</th>
            <td>{r.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * Chart chrome: unit caption + UTC note, a two-tick Y axis (max / 0) and the aligned
 * bar & date rows. `bars` and `labels` are separate rows sharing the same
 * `flex gap-2` grid so the baseline is one continuous line under all columns.
 *
 * The drawn half is `aria-hidden` because `table` restates it exactly; `note` stays
 * outside so caveats like「BYOK 记账恒为 0」are announced.
 */
function TrendFrame({
  unit,
  axisMax,
  note,
  bars,
  labels,
  table,
}: {
  unit: string;
  axisMax: string;
  note?: string;
  bars: ReactNode;
  labels: ReactNode;
  table: ReactNode;
}) {
  return (
    <div>
      {table}
      <div aria-hidden>
        <div className="mb-2 flex items-baseline justify-between gap-3 text-muted-foreground text-xs">
          <span>{unit}</span>
          <span title={UTC_WINDOW_HINT}>横轴按 UTC 日</span>
        </div>
        <div className="flex items-stretch gap-3">
          {/* pb-6 = 日期标签行（text-xs 16px + mt-2 8px），让「0」刻度落在柱底基线上。 */}
          <div className="flex shrink-0 flex-col justify-between pb-6 text-right text-muted-foreground text-xs tabular-nums">
            <span>{axisMax}</span>
            <span>0</span>
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex h-28 items-end gap-2 border-border border-b">
              {bars}
            </div>
            <div className="mt-2 flex gap-2">{labels}</div>
          </div>
        </div>
      </div>
      {note && <p className="mt-2 text-muted-foreground text-xs">{note}</p>}
    </div>
  );
}

/** 没有任何日分桶时的空态——与其它四态同一套语汇，不是一只空框。 */
function TrendEmpty({ description }: { description: string }) {
  return (
    <EmptyState
      icon={BarChart3}
      title="暂无趋势数据"
      description={description}
      className="py-8"
    />
  );
}

/** 日期标签行：与柱列同为 flex-1，保证轴刻度逐日对齐。 */
function dayLabels(dates: string[]): ReactNode {
  return dates.map((date) => (
    <span
      key={date}
      className="flex-1 text-center text-muted-foreground text-xs tabular-nums"
    >
      {mmdd(date)}
    </span>
  ));
}

export function CostTrendBars({
  data,
  currency,
}: {
  data: DailyCost[];
  /**
   * 该趋势金额的币种。逐日行（`DailyCost`）不带 `currency`，调用方须从同窗口的
   * `CostBreakdown` 带下来——同一账本窗口内币种唯一，且平台不做汇率换算。
   */
  currency?: string | null;
}) {
  if (data.length === 0) {
    return <TrendEmpty description="近 7 日（UTC）没有可绘制的成本分桶。" />;
  }
  const max = Math.max(0, ...data.map((d) => d.cost_total));
  const bars = data.map((d) => (
    <div
      key={d.date}
      // tooltip 挂在整根柱列（含柱子上方空白）上，否则 1 元以下的日子只有几 px 可悬停。
      title={`${d.date}（UTC）· ${fmtMoney(nanoToMajor(d.cost_total), currency)}`}
      className="flex h-full flex-1 items-end"
    >
      {d.cost_total > 0 && (
        <div
          data-testid="cost-bar"
          className="w-full rounded-t-md bg-primary/80"
          style={{ height: `${Math.max((d.cost_total / max) * 100, 2)}%` }}
        />
      )}
    </div>
  ));
  const symbol = currencySymbol(currency).trim();
  return (
    <TrendFrame
      unit={`单位：${symbol} · 每日记账成本`}
      axisMax={fmtMoney(nanoToMajor(max), currency)}
      note={max > 0 ? undefined : "区间内无记账花销（BYOK 模式记账恒为 0）"}
      bars={bars}
      labels={dayLabels(data.map((d) => d.date))}
      table={
        <TrendTable
          caption={`近 7 日每日记账成本（按 UTC 日 · 单位 ${symbol}）`}
          valueHeader="记账成本"
          rows={data.map((d) => ({
            date: d.date,
            value: fmtMoney(nanoToMajor(d.cost_total), currency),
          }))}
        />
      }
    />
  );
}

/** 7-day turn bars with the error segment stacked (destructive) atop the ok segment. */
export function TurnTrendBars({ data }: { data: DailyTurns[] }) {
  if (data.length === 0) {
    return <TrendEmpty description="近 7 日（UTC）没有可绘制的回合分桶。" />;
  }
  const max = Math.max(0, ...data.map((d) => d.turns));
  const bars = data.map((d) => {
    const ok = Math.max(d.turns - d.errors, 0);
    const pct = (n: number) => (max > 0 ? (n / max) * 100 : 0);
    return (
      <div
        key={d.date}
        title={`${d.date}（UTC）· 回合 ${fmtInt(d.turns)} · 错误 ${fmtInt(d.errors)}`}
        className="flex h-full flex-1 flex-col justify-end"
      >
        {d.errors > 0 && (
          <div
            data-testid="turn-error-bar"
            className="w-full rounded-t-md bg-destructive/80"
            style={{ height: `${Math.max(pct(d.errors), 2)}%` }}
          />
        )}
        {ok > 0 && (
          <div
            data-testid="turn-bar"
            className={cn(
              "w-full bg-primary/80",
              d.errors > 0 ? "" : "rounded-t-md",
            )}
            style={{ height: `${Math.max(pct(ok), 2)}%` }}
          />
        )}
      </div>
    );
  });
  return (
    <TrendFrame
      unit="单位：回合 · 红色为错误"
      axisMax={fmtInt(max)}
      note={max > 0 ? undefined : "区间内无回合"}
      bars={bars}
      labels={dayLabels(data.map((d) => d.date))}
      table={
        <TrendTable
          caption="近 7 日每日回合与错误数（按 UTC 日）"
          valueHeader="回合 / 错误"
          rows={data.map((d) => ({
            date: d.date,
            value: `${fmtInt(d.turns)} / ${fmtInt(d.errors)}`,
          }))}
        />
      }
    />
  );
}
