import {
  buildLedgerMap,
  extractLedgerId,
  ledgerBadgeLabel,
  ledgerDateLabel,
  mergeEvidenceLedger,
} from "@/lib/evidenceLedger";
import type { EvidenceLedgerEntry } from "@/types/events";
import { describe, expect, it } from "vitest";

const entry = (
  partial: Partial<EvidenceLedgerEntry> & { id: string },
): EvidenceLedgerEntry => ({
  id: partial.id,
  url: partial.url ?? "",
  title: partial.title ?? "",
  snippet: partial.snippet ?? "",
  site: partial.site ?? "",
  date: partial.date ?? "",
  tier: partial.tier ?? "unknown",
  side_key: partial.side_key ?? "",
});

describe("extractLedgerId", () => {
  it("extracts pure #eN", () => {
    expect(extractLedgerId("#e3")).toBe("#e3");
  });

  it("extracts #eN from dual-write note", () => {
    expect(extractLedgerId("街访数据 #e3")).toBe("#e3");
  });

  it("returns null for free-text legacy notes", () => {
    expect(extractLedgerId("2024年报")).toBeNull();
  });
});

describe("mergeEvidenceLedger", () => {
  it("appends new ids and overwrites same id", () => {
    const a = entry({ id: "#e1", site: "a.gov.cn" });
    const b = entry({ id: "#e2", site: "b.com" });
    const b2 = entry({ id: "#e2", site: "b2.com", title: "updated" });
    expect(mergeEvidenceLedger([a], [b, b2])).toEqual([a, b2]);
  });
});

describe("ledger display helpers", () => {
  it("prefers site then title then id", () => {
    expect(ledgerBadgeLabel(entry({ id: "#e1", site: "court.gov.cn" }))).toBe(
      "court.gov.cn",
    );
    expect(ledgerBadgeLabel(entry({ id: "#e1", title: "判决书" }))).toBe(
      "判决书",
    );
    expect(ledgerBadgeLabel(entry({ id: "#e1" }))).toBe("#e1");
  });

  it("maps empty date", () => {
    expect(ledgerDateLabel("")).toBe("日期未知");
    expect(ledgerDateLabel("2024-01-01")).toBe("2024-01-01");
  });

  it("buildLedgerMap keys by id", () => {
    const m = buildLedgerMap([entry({ id: "#e1" }), entry({ id: "#e2" })]);
    expect(m.get("#e1")?.id).toBe("#e1");
    expect(m.get("#e9")).toBeUndefined();
  });
});
