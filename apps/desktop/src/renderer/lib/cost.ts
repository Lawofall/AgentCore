/**
 * Pure cost-derivation helper for the turn cost row (§7.3A). Framework-free on
 * purpose: the money math lives here so it is unit-testable without a DOM, and
 * the components only render the result. (Per-Agent money now shows directly on
 * each graph node from `run.cost`, §7.3B — no payroll split needed.)
 *
 * Money is integer nano throughout (1 unit = 1e9). Platform billed is credits;
 * BYOK is the official CNY card shown as ¥. Legacy community-USD estimates
 * still carry `estimated_currency=USD` (≈$). This never re-prices and **never
 * converts**; it only sums already-priced run totals within a unit + currency.
 */

import {
  type CostMoney,
  type SplitCostMoney,
  higherSplitCost,
  mergeSplitCost,
  splitCostMoney,
  sumSplitCosts,
} from "@/lib/format";

export type CostLeaf = {
  total: number;
  currency?: string | null;
  estimated_total?: number | null;
  estimated_currency?: string | null;
  pricing_source?: string | null;
};

export type DisplayMoney = CostMoney;

const EMPTY_SPLIT: SplitCostMoney = { billed: null, extra: null };

/**
 * The turn cost to display (§7.3A): prefer the authoritative `turnTotal` from
 * `message_end`; when absent (a stopped/crashed turn never gets one) fall back to
 * the sum of the runs that did finish — a lower bound, but it still shows what the
 * team已花. Returns null when there is nothing real to show, so callers render
 * 「—」/ nothing rather than「0.00 积分」(§7.5).
 *
 * Note `turnTotal` of 0 is returned verbatim (it is a known total, not "unknown");
 * the caller still gates display on `> 0`.
 */
export function resolveTurnCost(
  turnTotal: number | null,
  runCosts: number[],
): number | null {
  const runTotal = runCosts.reduce((n, c) => n + c, 0);
  return turnTotal ?? (runTotal > 0 ? runTotal : null);
}

function hasSplitMoney(split: SplitCostMoney): boolean {
  return (
    (split.billed != null && split.billed.nano > 0) ||
    (split.extra != null && split.extra.nano > 0)
  );
}

/**
 * Turn display bill: platform credits and BYOK ¥ stay two columns. Null = nothing
 * to show. `turnCost.total` already includes the BYOK copy (same card, same CNY).
 */
export function resolveTurnDisplayBill(
  turnCost: CostLeaf | null | undefined,
  runCosts: Array<CostLeaf | null | undefined>,
): SplitCostMoney | null {
  if (turnCost) {
    const split = splitCostMoney(turnCost);
    return hasSplitMoney(split) ? split : EMPTY_SPLIT;
  }
  const split = sumSplitCosts(runCosts);
  return hasSplitMoney(split) ? split : null;
}

/** @deprecated Use {@link resolveTurnDisplayBill}; kept for single-column callers. */
export function resolveTurnDisplayMoney(
  turnCost: CostLeaf | null | undefined,
  runCosts: Array<CostLeaf | null | undefined>,
): DisplayMoney | null {
  const split = resolveTurnDisplayBill(turnCost, runCosts);
  if (!split) return null;
  return split.billed ?? split.extra;
}

export type SpendUsage = {
  input: number;
  output: number;
  reasoning: number;
  cache_hit: number;
  cache_miss: number;
};

export type SpendRun = {
  id: string;
  status: string;
  role: string | null;
  label: string;
  cost: CostLeaf | null | undefined;
  usage: SpendUsage | null | undefined;
};

export type SpendLine = {
  key: string;
  label: string;
  usage: SpendUsage | null;
  billed: DisplayMoney | null;
  extra: DisplayMoney | null;
  provisional: boolean;
};

export type ReplyInvoice = {
  billed: DisplayMoney | null;
  extra: DisplayMoney | null;
  provisional: boolean;
  lines: SpendLine[];
};

const OPEN_RUN = new Set(["pending", "running"]);

function splitOf(cost: CostLeaf | null | undefined): SplitCostMoney {
  return splitCostMoney(cost);
}

function lineFromRun(run: SpendRun): SpendLine {
  const split = splitOf(run.cost);
  return {
    key: run.id,
    label: run.label,
    usage: run.usage ?? null,
    billed: split.billed,
    extra: split.extra,
    provisional: OPEN_RUN.has(run.status),
  };
}

/**
 * The reply's bill.
 *
 * Platform credits and BYOK ¥ stay two columns (captain + teammates in each).
 * While someone is still running, or the reply itself has not closed, the
 * number is what has already come back and is labeled「至今」. Once every run
 * is terminal and the bubble has closed, the larger of the ledger snapshot and
 * the run sum is the settled bill (late workers can finish after `message_end`;
 * the ledger can also hold a row the graph does not).
 *
 * Captain calls that return before the team graph exists stay on
 * `soloAccrued` and fill the captain line so they are not dropped.
 */
export function resolveReplyInvoice(input: {
  ledger: CostLeaf | null | undefined;
  runs: SpendRun[];
  soloAccrued: CostLeaf | null | undefined;
  soloUsage?: SpendUsage | null;
  bubbleLive: boolean;
}): ReplyInvoice {
  const captainRuns = input.runs.filter((run) => run.role === "captain");
  const otherRuns = input.runs.filter((run) => run.role !== "captain");
  const solo = splitOf(input.soloAccrued);
  const captainNode =
    input.runs.length === 0
      ? EMPTY_SPLIT
      : (resolveTurnDisplayBill(
          null,
          captainRuns.map((run) => run.cost),
        ) ?? EMPTY_SPLIT);
  const captainSplit =
    captainRuns.length <= 1
      ? higherSplitCost(captainNode, solo)
      : hasSplitMoney(captainNode)
        ? captainNode
        : solo;
  const otherSplit =
    resolveTurnDisplayBill(
      null,
      otherRuns.map((run) => run.cost),
    ) ?? EMPTY_SPLIT;
  const accrued = mergeSplitCost(otherSplit, captainSplit);
  const ledger = splitOf(input.ledger);
  const anyOpen = input.runs.some((run) => OPEN_RUN.has(run.status));
  const provisional =
    anyOpen ||
    (input.bubbleLive && (hasSplitMoney(accrued) || hasSplitMoney(ledger)));
  const bill = higherSplitCost(ledger, accrued);

  const otherLines = otherRuns
    .filter((run) => run.usage || hasSplitMoney(splitOf(run.cost)))
    .map(lineFromRun);
  const captainLines: SpendLine[] = [];
  if (captainRuns.length === 1) {
    const run = captainRuns[0];
    if (run.usage || input.soloUsage || hasSplitMoney(captainSplit)) {
      captainLines.push({
        key: run.id,
        label: run.label || "队长",
        usage: run.usage ?? input.soloUsage ?? null,
        billed: captainSplit.billed,
        extra: captainSplit.extra,
        provisional: OPEN_RUN.has(run.status),
      });
    }
  } else if (
    captainRuns.length === 0 &&
    hasSplitMoney(captainSplit) &&
    (input.bubbleLive || !hasSplitMoney(ledger))
  ) {
    captainLines.push({
      key: "captain",
      label: "队长",
      usage: input.soloUsage ?? null,
      billed: captainSplit.billed,
      extra: captainSplit.extra,
      provisional: input.bubbleLive,
    });
  } else {
    for (const run of captainRuns) {
      if (run.usage || hasSplitMoney(splitOf(run.cost))) {
        captainLines.push(lineFromRun(run));
      }
    }
  }

  return {
    billed: bill.billed,
    extra: bill.extra,
    provisional: Boolean(provisional && hasSplitMoney(bill)),
    lines: [...captainLines, ...otherLines],
  };
}

export function sumSpendUsage(lines: SpendLine[]): SpendUsage | null {
  const acc: SpendUsage = {
    input: 0,
    output: 0,
    reasoning: 0,
    cache_hit: 0,
    cache_miss: 0,
  };
  let any = false;
  for (const line of lines) {
    const usage = line.usage;
    if (!usage || (usage.input <= 0 && usage.output <= 0)) continue;
    any = true;
    acc.input += usage.input;
    acc.output += usage.output;
    acc.reasoning += usage.reasoning;
    acc.cache_hit += usage.cache_hit;
    acc.cache_miss += usage.cache_miss;
  }
  return any ? acc : null;
}
