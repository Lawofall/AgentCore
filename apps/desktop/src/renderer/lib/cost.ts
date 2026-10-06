/**
 * Pure cost-derivation helper for the turn cost row (§7.3A). Framework-free on
 * purpose: the money math lives here so it is unit-testable without a DOM, and
 * the components only render the result. (Per-Agent money now shows directly on
 * each graph node from `run.cost`, §7.3B — no payroll split needed.)
 *
 * Money is integer nano throughout (1 unit = 1e9). User-facing amounts are the
 * curated CNY nominal — platform billed and BYOK display copy of the same card.
 * Legacy community-USD estimates still carry `estimated_currency=USD` (≈$).
 * This never re-prices and **never converts**; it only sums already-priced run
 * totals within a single currency.
 */

export type CostLeaf = {
  total: number;
  currency?: string | null;
  estimated_total?: number | null;
  estimated_currency?: string | null;
  pricing_source?: string | null;
};

export type DisplayMoney = {
  nano: number;
  /** True only for leftover USD community estimates (≈$). CNY is a real bill. */
  estimated: boolean;
  /** ISO code of {@link nano}. Renderers pick the symbol from this, never guess. */
  currency: string;
};

/** 无 FX：跨币种金额不可相加，所以合计只在同一币种内累加。 */
const DEFAULT_CURRENCY = "CNY";

function isUsdEstimate(currency: string): boolean {
  return currency.toUpperCase() === "USD";
}

/**
 * The turn cost to display (§7.3A): prefer the authoritative `turnTotal` from
 * `message_end`; when absent (a stopped/crashed turn never gets one) fall back to
 * the sum of the runs that did finish — a lower bound, but it still shows what the
 * team已花. Returns null when there is nothing real to show, so callers render
 * 「—」/ nothing rather than「¥0.00」(§7.5).
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

function displayFromEstimate(nano: number, currency: string): DisplayMoney {
  return {
    nano,
    estimated: isUsdEstimate(currency),
    currency,
  };
}

/**
 * Turn display money: `total` (product nominal, CNY) wins; else `estimated_total`
 * (BYOK slice, or legacy USD). Null = nothing to show.
 */
export function resolveTurnDisplayMoney(
  turnCost: CostLeaf | null | undefined,
  runCosts: Array<CostLeaf | null | undefined>,
): DisplayMoney | null {
  if (turnCost) {
    const billedCurrency = turnCost.currency || DEFAULT_CURRENCY;
    if (turnCost.total > 0) {
      return {
        nano: turnCost.total,
        estimated: false,
        currency: billedCurrency,
      };
    }
    const est = turnCost.estimated_total;
    if (est != null && est > 0) {
      return displayFromEstimate(
        est,
        turnCost.estimated_currency || billedCurrency,
      );
    }
    return { nano: 0, estimated: false, currency: billedCurrency };
  }
  let billed = 0;
  let billedCurrency: string | null = null;
  let estimated = 0;
  let estimatedCurrency: string | null = null;
  for (const c of runCosts) {
    if (!c) continue;
    if (c.total > 0) {
      billed += c.total;
      billedCurrency ??= c.currency || DEFAULT_CURRENCY;
    }
    const est = c.estimated_total ?? 0;
    if (est > 0) {
      estimated += est;
      estimatedCurrency ??=
        c.estimated_currency || c.currency || DEFAULT_CURRENCY;
    }
  }
  if (billed > 0) {
    return {
      nano: billed,
      estimated: false,
      currency: billedCurrency ?? DEFAULT_CURRENCY,
    };
  }
  if (estimated > 0) {
    return displayFromEstimate(
      estimated,
      estimatedCurrency ?? DEFAULT_CURRENCY,
    );
  }
  return null;
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
  money: DisplayMoney | null;
  provisional: boolean;
};

export type ReplyInvoice = {
  money: DisplayMoney | null;
  provisional: boolean;
  lines: SpendLine[];
};

const OPEN_RUN = new Set(["pending", "running"]);

function moneyOf(cost: CostLeaf | null | undefined): DisplayMoney | null {
  const money = resolveTurnDisplayMoney(cost, []);
  return money && money.nano > 0 ? money : null;
}

function higherMoney(
  left: DisplayMoney | null,
  right: DisplayMoney | null,
): DisplayMoney | null {
  if (!left) return right;
  if (!right) return left;
  if (left.currency !== right.currency || left.estimated !== right.estimated) {
    return left;
  }
  return left.nano >= right.nano ? left : right;
}

function addMoney(
  left: DisplayMoney | null,
  right: DisplayMoney | null,
): DisplayMoney | null {
  if (!left) return right;
  if (!right) return left;
  if (left.currency !== right.currency || left.estimated !== right.estimated) {
    return left;
  }
  return {
    nano: left.nano + right.nano,
    estimated: left.estimated,
    currency: left.currency,
  };
}

function lineFromRun(run: SpendRun): SpendLine {
  return {
    key: run.id,
    label: run.label,
    usage: run.usage ?? null,
    money: moneyOf(run.cost),
    provisional: OPEN_RUN.has(run.status),
  };
}

/**
 * The reply's bill.
 *
 * One number: captain + teammates. While someone is still running, or the
 * reply itself has not closed, the number is what has already come back and
 * is labeled「至今」. Once every run is terminal and the bubble has closed,
 * the larger of the ledger snapshot and the run sum is the settled bill
 * (late workers can finish after `message_end`; the ledger can also hold a
 * row the graph does not).
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
  const solo = moneyOf(input.soloAccrued);
  const captainNode =
    input.runs.length === 0
      ? null
      : resolveTurnDisplayMoney(
          null,
          captainRuns.map((run) => run.cost),
        );
  const captainNodeMoney =
    captainNode && captainNode.nano > 0 ? captainNode : null;
  const captainMoney =
    captainRuns.length <= 1
      ? higherMoney(captainNodeMoney, solo)
      : (captainNodeMoney ?? solo);
  const otherMoney = resolveTurnDisplayMoney(
    null,
    otherRuns.map((run) => run.cost),
  );
  const accrued = addMoney(
    otherMoney && otherMoney.nano > 0 ? otherMoney : null,
    captainMoney,
  );
  const ledger = moneyOf(input.ledger);
  const anyOpen = input.runs.some((run) => OPEN_RUN.has(run.status));
  const provisional =
    anyOpen || (input.bubbleLive && (accrued != null || ledger != null));
  // Ledger first: same currency takes the larger (late workers or a ledger
  // row the graph does not carry); a currency mismatch keeps the ledger.
  const money = higherMoney(ledger, accrued);

  const otherLines = otherRuns
    .filter((run) => run.usage || moneyOf(run.cost))
    .map(lineFromRun);
  const captainLines: SpendLine[] = [];
  if (captainRuns.length === 1) {
    const run = captainRuns[0];
    if (run.usage || input.soloUsage || captainMoney) {
      captainLines.push({
        key: run.id,
        label: run.label || "队长",
        usage: run.usage ?? input.soloUsage ?? null,
        money: captainMoney,
        provisional: OPEN_RUN.has(run.status),
      });
    }
  } else if (
    captainRuns.length === 0 &&
    captainMoney &&
    (input.bubbleLive || ledger == null)
  ) {
    captainLines.push({
      key: "captain",
      label: "队长",
      usage: input.soloUsage ?? null,
      money: captainMoney,
      provisional: input.bubbleLive,
    });
  } else {
    for (const run of captainRuns) {
      if (run.usage || moneyOf(run.cost)) captainLines.push(lineFromRun(run));
    }
  }

  return {
    money: money && money.nano > 0 ? money : null,
    provisional: Boolean(provisional && money && money.nano > 0),
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
