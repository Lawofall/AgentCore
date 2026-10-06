import {
  type SpendRun,
  resolveReplyInvoice,
  resolveTurnCost,
  resolveTurnDisplayMoney,
} from "@/lib/cost";
import { describe, expect, it } from "vitest";

describe("resolveTurnCost", () => {
  it("prefers the authoritative turn total when known", () => {
    expect(resolveTurnCost(28, [10, 5])).toBe(28);
  });

  it("returns a known total of 0 verbatim (known, not unknown)", () => {
    expect(resolveTurnCost(0, [10])).toBe(0);
  });

  it("falls back to the run sum when there is no turn total (stopped/crashed)", () => {
    expect(resolveTurnCost(null, [10, 5])).toBe(15);
  });

  it("returns null when there is nothing real to show (无花销不显，§7.5)", () => {
    expect(resolveTurnCost(null, [0, 0])).toBeNull();
    expect(resolveTurnCost(null, [])).toBeNull();
  });
});

describe("resolveTurnDisplayMoney", () => {
  it("prefers turn billed total, then estimated_total", () => {
    expect(
      resolveTurnDisplayMoney({ total: 28, estimated_total: 99 }, []),
    ).toEqual({ nano: 28, estimated: false, currency: "CNY" });
    expect(
      resolveTurnDisplayMoney({ total: 0, estimated_total: 99 }, []),
    ).toEqual({ nano: 99, estimated: false, currency: "CNY" });
  });

  it("falls back to run estimated sum when turn cost is absent", () => {
    expect(
      resolveTurnDisplayMoney(null, [
        { total: 0, estimated_total: 10 },
        { total: 0, estimated_total: 5 },
      ]),
    ).toEqual({ nano: 15, estimated: false, currency: "CNY" });
  });

  it("sums run billed totals when turn cost is absent", () => {
    expect(
      resolveTurnDisplayMoney(null, [
        { total: 10, pricing_source: "curated" },
        { total: 5, pricing_source: "curated" },
      ]),
    ).toEqual({ nano: 15, estimated: false, currency: "CNY" });
  });

  it("legacy USD estimates still carry ≈$ (estimated: true)", () => {
    expect(
      resolveTurnDisplayMoney(
        { total: 0, estimated_total: 99, estimated_currency: "USD" },
        [],
      ),
    ).toEqual({ nano: 99, estimated: true, currency: "USD" });
    expect(
      resolveTurnDisplayMoney(null, [
        { total: 0, estimated_total: 10, estimated_currency: "USD" },
        { total: 0, estimated_total: 5, estimated_currency: "USD" },
      ]),
    ).toEqual({ nano: 15, estimated: true, currency: "USD" });
  });

  it("returns null when nothing real to show", () => {
    expect(resolveTurnDisplayMoney(null, [])).toBeNull();
    expect(
      resolveTurnDisplayMoney(null, [{ total: 0 }, { total: 0 }]),
    ).toBeNull();
  });
});

function spendRun(over: Partial<SpendRun> & Pick<SpendRun, "id">): SpendRun {
  return {
    status: "completed",
    role: "member",
    label: "队员",
    cost: null,
    usage: null,
    ...over,
  };
}

const cny = (total: number) => ({ total, currency: "CNY" });

describe("resolveReplyInvoice", () => {
  it("sums an open team and marks the bill 至今", () => {
    const invoice = resolveReplyInvoice({
      ledger: null,
      bubbleLive: true,
      soloAccrued: null,
      runs: [
        spendRun({
          id: "cap",
          role: "captain",
          label: "队长",
          status: "completed",
          cost: cny(5),
        }),
        spendRun({
          id: "w",
          status: "running",
          cost: cny(3),
        }),
      ],
    });
    expect(invoice.money).toEqual({
      nano: 8,
      estimated: false,
      currency: "CNY",
    });
    expect(invoice.provisional).toBe(true);
    expect(invoice.lines.map((line) => line.provisional)).toEqual([
      false,
      true,
    ]);
  });

  it("settles on the larger of the ledger and the run sum", () => {
    const late = resolveReplyInvoice({
      ledger: cny(5),
      bubbleLive: false,
      soloAccrued: null,
      runs: [
        spendRun({ id: "cap", role: "captain", cost: cny(5) }),
        spendRun({ id: "w", cost: cny(3) }),
      ],
    });
    expect(late.money?.nano).toBe(8);
    expect(late.provisional).toBe(false);

    const ledgerExtra = resolveReplyInvoice({
      ledger: cny(10),
      bubbleLive: false,
      soloAccrued: null,
      runs: [spendRun({ id: "cap", role: "captain", cost: cny(8) })],
    });
    expect(ledgerExtra.money?.nano).toBe(10);
    expect(ledgerExtra.provisional).toBe(false);
  });

  it("keeps the ledger when currencies differ", () => {
    const invoice = resolveReplyInvoice({
      ledger: cny(5),
      bubbleLive: false,
      soloAccrued: null,
      runs: [
        spendRun({
          id: "w",
          cost: { total: 9, currency: "USD", estimated_total: 9 },
        }),
      ],
    });
    expect(invoice.money).toEqual({
      nano: 5,
      estimated: false,
      currency: "CNY",
    });
  });

  it("fills the captain line from solo accrued without double-counting", () => {
    const invoice = resolveReplyInvoice({
      ledger: null,
      bubbleLive: true,
      soloAccrued: cny(5),
      runs: [
        spendRun({
          id: "cap",
          role: "captain",
          label: "队长",
          status: "running",
          cost: cny(3),
        }),
      ],
    });
    expect(invoice.money?.nano).toBe(5);
    expect(invoice.lines).toHaveLength(1);
    expect(invoice.lines[0]?.money?.nano).toBe(5);
    expect(invoice.lines[0]?.provisional).toBe(true);
  });

  it("shows solo accrued as 至今 while the reply is live, then the ledger", () => {
    const live = resolveReplyInvoice({
      ledger: null,
      bubbleLive: true,
      soloAccrued: cny(2),
      soloUsage: {
        input: 10,
        output: 1,
        reasoning: 0,
        cache_hit: 0,
        cache_miss: 0,
      },
      runs: [],
    });
    expect(live.provisional).toBe(true);
    expect(live.money?.nano).toBe(2);
    expect(live.lines[0]?.label).toBe("队长");
    expect(live.lines[0]?.usage?.input).toBe(10);

    const closed = resolveReplyInvoice({
      ledger: cny(5),
      bubbleLive: false,
      soloAccrued: cny(2),
      runs: [],
    });
    expect(closed.provisional).toBe(false);
    expect(closed.money?.nano).toBe(5);
    expect(closed.lines).toEqual([]);
  });
});
