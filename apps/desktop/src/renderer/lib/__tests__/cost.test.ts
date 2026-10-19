import {
  type SpendRun,
  resolveReplyInvoice,
  resolveTurnCost,
  resolveTurnDisplayBill,
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

const credit = (nano: number) => ({
  nano,
  estimated: false,
  currency: "CNY",
  unit: "credit" as const,
});
const cashCny = (nano: number) => ({
  nano,
  estimated: false,
  currency: "CNY",
  unit: "cash" as const,
});
const cashUsd = (nano: number) => ({
  nano,
  estimated: true,
  currency: "USD",
  unit: "cash" as const,
});

describe("resolveTurnDisplayBill", () => {
  it("splits a mixed turn: total already includes the BYOK copy", () => {
    expect(
      resolveTurnDisplayBill({ total: 127, estimated_total: 99 }, []),
    ).toEqual({ billed: credit(28), extra: cashCny(99) });
    expect(
      resolveTurnDisplayBill({ total: 0, estimated_total: 99 }, []),
    ).toEqual({ billed: null, extra: cashCny(99) });
  });

  it("falls back to run estimated sum when turn cost is absent", () => {
    expect(
      resolveTurnDisplayBill(null, [
        { total: 0, estimated_total: 10 },
        { total: 0, estimated_total: 5 },
      ]),
    ).toEqual({ billed: null, extra: cashCny(15) });
  });

  it("sums run billed totals when turn cost is absent", () => {
    expect(
      resolveTurnDisplayBill(null, [
        { total: 10, pricing_source: "curated" },
        { total: 5, pricing_source: "curated" },
      ]),
    ).toEqual({ billed: credit(15), extra: null });
  });

  it("legacy USD estimates still carry ≈$ (estimated: true)", () => {
    expect(
      resolveTurnDisplayBill(
        { total: 0, estimated_total: 99, estimated_currency: "USD" },
        [],
      ),
    ).toEqual({ billed: null, extra: cashUsd(99) });
    expect(
      resolveTurnDisplayBill(null, [
        { total: 0, estimated_total: 10, estimated_currency: "USD" },
        { total: 0, estimated_total: 5, estimated_currency: "USD" },
      ]),
    ).toEqual({ billed: null, extra: cashUsd(15) });
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
    expect(invoice.billed).toEqual(credit(8));
    expect(invoice.extra).toBeNull();
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
    expect(late.billed?.nano).toBe(8);
    expect(late.provisional).toBe(false);

    const ledgerExtra = resolveReplyInvoice({
      ledger: cny(10),
      bubbleLive: false,
      soloAccrued: null,
      runs: [spendRun({ id: "cap", role: "captain", cost: cny(8) })],
    });
    expect(ledgerExtra.billed?.nano).toBe(10);
    expect(ledgerExtra.provisional).toBe(false);
  });

  it("keeps platform credits and leftover USD on separate columns", () => {
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
    expect(invoice.billed).toEqual(credit(5));
    expect(invoice.extra).toEqual(cashUsd(9));
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
    expect(invoice.billed?.nano).toBe(5);
    expect(invoice.lines).toHaveLength(1);
    expect(invoice.lines[0]?.billed?.nano).toBe(5);
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
    expect(live.billed?.nano).toBe(2);
    expect(live.lines[0]?.label).toBe("队长");
    expect(live.lines[0]?.usage?.input).toBe(10);

    const closed = resolveReplyInvoice({
      ledger: cny(5),
      bubbleLive: false,
      soloAccrued: cny(2),
      runs: [],
    });
    expect(closed.provisional).toBe(false);
    expect(closed.billed?.nano).toBe(5);
    expect(closed.lines).toEqual([]);
  });

  it("splits mixed platform + BYOK on the same reply", () => {
    const invoice = resolveReplyInvoice({
      ledger: { total: 8, estimated_total: 3, currency: "CNY" },
      bubbleLive: false,
      soloAccrued: null,
      runs: [
        spendRun({
          id: "cap",
          role: "captain",
          cost: { total: 5, currency: "CNY" },
        }),
        spendRun({
          id: "w",
          cost: { total: 3, estimated_total: 3, currency: "CNY" },
        }),
      ],
    });
    expect(invoice.billed).toEqual(credit(5));
    expect(invoice.extra).toEqual(cashCny(3));
  });
});
