import { Button } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  COST_ESTIMATE_HINT,
  COST_ESTIMATE_LABEL,
  COST_UNPRICED_HINT,
  COST_UNPRICED_LABEL,
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
 * Per-run resource ledger (§7.3B power detail) — the single place a run's full
 * raw token + cost breakdown lives. Defaults expanded. All-zero cost renders as
 * 「—」(§7.5), not「¥0.00」. BYOK with estimate shows ≈¥ + 估算标注.
 */
export function ResourceSection({
  run,
  agent,
  defaultExpanded,
  keyBase,
}: {
  run: RunNode;
  agent: AgentState;
  defaultExpanded: boolean;
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
          <MetricRow label="思考" value={reasoningMeta(agent.thinking).label} />
          {model && <MetricRow label="模型" value={model} mono />}

          {money != null && money.nano > 0 && (
            <div>
              <MetricRow
                label={
                  money.estimated ? `成本（${COST_ESTIMATE_LABEL}）` : "成本"
                }
                value={formatDisplayCost(
                  money.nano,
                  money.estimated,
                  money.currency,
                )}
              />
              {money.estimated ? (
                <SimpleTooltip label={COST_ESTIMATE_HINT}>
                  <p className="mt-0.5 cursor-default text-xs text-muted-foreground">
                    {COST_ESTIMATE_HINT}
                  </p>
                </SimpleTooltip>
              ) : cost ? (
                // 分项与 total 同属这条 run 的一张价卡 → 同 cost.currency。
                <p className="mt-0.5 text-xs text-muted-foreground">
                  输入 {formatDisplayCost(cost.input, false, cost.currency)} ·
                  输出 {formatDisplayCost(cost.output, false, cost.currency)}
                  {cost.cached > 0 && (
                    <>
                      {" "}
                      · 缓存省{" "}
                      {formatDisplayCost(cost.cached, false, cost.currency)}
                    </>
                  )}
                </p>
              ) : null}
            </div>
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
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {cache?.billedAsMiss
                    ? `${CACHE_BILLED_AS_MISS_LABEL} ${formatCompact(cache.cacheMiss)}`
                    : `命中 ${formatCompact(usage.cache_hit)} · 未命中 ${formatCompact(usage.cache_miss)} · 缓存率 ${cache?.hitRatePercent ?? 0}%`}
                </p>
              </div>
              <div>
                <MetricRow
                  label="输出 token"
                  value={formatCompact(usage.output)}
                />
                <p className="mt-0.5 text-xs text-muted-foreground">
                  推理 {formatCompact(usage.reasoning)}
                </p>
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
