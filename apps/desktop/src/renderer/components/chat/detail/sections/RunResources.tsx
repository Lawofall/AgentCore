import { Button } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  COST_ESTIMATE_HINT,
  COST_ESTIMATE_LABEL,
  COST_UNPRICED_HINT,
  COST_UNPRICED_LABEL,
  formatAlignedCostParts,
  formatCompact,
  formatCostCaption,
  formatDisplayCost,
  pickCostMoney,
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
 * breakdown lives. Defaults collapsed; header keeps the run ¥. All-zero cost
 * renders as「—」(§7.5), not「¥0.00」. BYOK with estimate shows ≈ + 估算标注.
 * `cost.cached` is the billed cache-hit portion (already inside input), not
 * savings vs miss price.
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
  const money = pickCostMoney(cost);
  const tokenTotal = usage ? usage.input + usage.output : 0;
  const byokHint =
    money?.estimated === true ||
    (cost?.estimated_total ?? 0) > 0 ||
    cost?.pricing_source === "unpriced";
  // 未计价 ≠ 估算：三层价卡全落空时连估算值都没有，标注要如实（拍板 2026-07-20）。
  const unpriced =
    cost?.pricing_source === "unpriced" && (money == null || money.nano <= 0);
  const byokLabel = unpriced ? COST_UNPRICED_LABEL : COST_ESTIMATE_LABEL;
  const byokTitle = unpriced ? COST_UNPRICED_HINT : COST_ESTIMATE_HINT;
  const costLabel =
    money != null && money.nano > 0
      ? formatCostCaption(money.nano, money.estimated, money.currency)
      : tokenTotal > 0 && byokHint
        ? `${formatCompact(tokenTotal)} tok · ${byokLabel}`
        : null;
  const cache = usage ? cacheUsageDisplay(usage) : null;
  const think = reasoningMeta(agent.thinking);
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
  const parts =
    cost != null && money != null && money.nano > 0 && !money.estimated
      ? formatAlignedCostParts(
          cost.input,
          cost.output,
          money.nano,
          cost.currency,
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
              title={byokHint ? byokTitle : undefined}
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
              {cost && cost.cached > 0 && (
                <p className="mt-0.5 text-xs tabular-nums text-muted-foreground">
                  其中缓存{" "}
                  {formatDisplayCost(cost.cached, false, cost.currency)}
                </p>
              )}
            </div>
          )}
          {money != null && money.nano > 0 && money.estimated && (
            <SimpleTooltip label={COST_ESTIMATE_HINT}>
              <p className="cursor-default text-xs text-muted-foreground">
                {COST_ESTIMATE_HINT}
              </p>
            </SimpleTooltip>
          )}
          {money != null &&
            money.nano <= 0 &&
            usage != null &&
            tokenTotal > 0 &&
            byokHint && (
              <MetricRow
                label={byokLabel}
                value={`${formatCompact(usage.input)}↑ / ${formatCompact(usage.output)}↓`}
              />
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
                    推理 {formatCompact(usage.reasoning)}
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
