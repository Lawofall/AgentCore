import { Button } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  COST_ESTIMATE_HINT,
  formatAlignedCostParts,
  formatCompact,
  formatCostBill,
  formatCostMoney,
  splitCostMoney,
} from "@/lib/format";
import { usePersistentDisclosure } from "@/stores/disclosure";
import {
  type AgentState,
  type RunNode,
  reasoningMeta,
} from "@/stores/execution";
import {
  CACHE_BILLED_AS_MISS_LABEL,
  cacheUsageDisplay,
} from "@agentcore/protocol-fold-kit";
import { ChevronDown, ChevronRight } from "lucide-react";
import { MetricRow } from "./shared";

/**
 * Per-run resource ledger — the single place a run's full token + cost
 * breakdown lives. Defaults collapsed; header keeps the run 积分 / ¥. All-zero
 * cost renders as「—」(§7.5), not「0.00 积分」. BYOK 有产品价目时出 ¥，tooltip
 * 「按官网价目」。`cost.cached` is the billed cache-hit portion (already inside
 * input), not savings vs miss price.
 */
export function ResourceSection({
  run,
  agent,
  defaultExpanded = false,
  keyBase,
}: {
  run: RunNode;
  agent: AgentState;
  defaultExpanded?: boolean;
  keyBase: string;
}) {
  const [expanded, setExpanded] = usePersistentDisclosure(
    `${keyBase}:resources`,
    defaultExpanded,
  );
  const { usage, cost, model } = run;
  const split = splitCostMoney(cost);
  const tokenTotal = usage ? usage.input + usage.output : 0;
  const byokHint = split.extra != null;
  const accrued = run.status === "running" || run.status === "pending";
  const billText = formatCostBill(split.billed, split.extra, accrued);
  const costLabel =
    billText ??
    (tokenTotal > 0
      ? `${formatCompact(tokenTotal)} tok${accrued ? " 至今" : ""}`
      : null);
  const cache = usage ? cacheUsageDisplay(usage) : null;
  const think = reasoningMeta(agent.thinking, run.reasoningEffort);
  const cacheLine =
    cache == null
      ? null
      : cache.billedAsMiss
        ? `${CACHE_BILLED_AS_MISS_LABEL} ${formatCompact(cache.cacheMiss)}`
        : cache.cacheHit > 0
          ? `缓存命中 ${formatCompact(cache.cacheHit)}${
              cache.hitRatePercent != null ? `（${cache.hitRatePercent}%）` : ""
            }`
          : null;
  const partsUnit = split.billed
    ? split.extra
      ? null
      : ("credit" as const)
    : split.extra && !split.extra.estimated
      ? ("cash" as const)
      : null;
  const partsMoney = split.billed ?? split.extra;
  const parts =
    cost != null && partsUnit != null && partsMoney != null
      ? formatAlignedCostParts(
          cost.input,
          cost.output,
          partsMoney.nano,
          partsMoney.currency,
          partsUnit,
        )
      : null;

  return (
    <section className="mb-4 last:mb-0">
      <Button
        variant="ghost"
        onClick={() => setExpanded((v) => !v)}
        className="h-auto w-full justify-start gap-1.5 px-0 py-0 hover:bg-transparent"
      >
        <span className="flex w-full items-center gap-1.5">
          {expanded ? (
            <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight
              size={14}
              className="shrink-0 text-muted-foreground"
            />
          )}
          <span className="flex-1 text-left text-xs font-medium text-muted-foreground">
            资源消耗
          </span>
          {costLabel && (
            <span
              className="text-xs tabular-nums text-muted-foreground"
              title={byokHint ? COST_ESTIMATE_HINT : undefined}
            >
              {costLabel}
            </span>
          )}
        </span>
      </Button>

      {expanded && (
        <div className="mt-2 space-y-2 rounded-lg bg-muted p-3">
          <div className="flex items-baseline justify-between gap-3">
            {model ? (
              <span
                className="min-w-0 truncate font-mono text-xs text-foreground"
                title={model}
              >
                {model}
              </span>
            ) : (
              <span className="text-xs text-muted-foreground">模型</span>
            )}
            <SimpleTooltip label={think.description}>
              <span className="shrink-0 cursor-default text-xs text-muted-foreground">
                {think.label}
              </span>
            </SimpleTooltip>
          </div>

          {parts && (
            <div>
              <p className="text-xs tabular-nums text-muted-foreground">
                输入 {parts.input} · 输出 {parts.output}
              </p>
              {cost && cost.cached > 0 && partsMoney && partsUnit && (
                <p className="mt-0.5 text-xs tabular-nums text-muted-foreground">
                  其中缓存{" "}
                  {formatCostMoney({
                    nano: cost.cached,
                    estimated: false,
                    currency: partsMoney.currency,
                    unit: partsUnit,
                  })}
                </p>
              )}
            </div>
          )}
          {byokHint && split.extra != null && (
            <SimpleTooltip label={COST_ESTIMATE_HINT}>
              <p className="cursor-default text-xs text-muted-foreground">
                {COST_ESTIMATE_HINT}
              </p>
            </SimpleTooltip>
          )}

          {usage && (
            <>
              <div>
                <MetricRow
                  label="输入 token"
                  value={formatCompact(usage.input)}
                />
                {cacheLine && (
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {cacheLine}
                  </p>
                )}
              </div>
              <div>
                <MetricRow
                  label="输出 token"
                  value={formatCompact(usage.output)}
                />
                {usage.reasoning > 0 && (
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    思考 {formatCompact(usage.reasoning)}
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
