import {
  COST_ESTIMATE_LABEL,
  chunksTailText,
  estimateTokensFromCharCount,
  formatBytes,
  formatBytesPerSecond,
  formatCompact,
  formatCost,
  formatCostCaption,
  formatDateDivider,
  formatDisplayCost,
  formatDownloadProgress,
  formatDuration,
  formatDurationSec,
  formatMessageTime,
  formatMessageTimeOfDay,
  pickCostMoney,
  stripDurationFaceSuffix,
  sumChunkChars,
  tailText,
} from "@/lib/format";
import { describe, expect, it, vi } from "vitest";

// 1 元 = 1e9 nano-CNY (ledger canonical unit).
const YUAN = 1_000_000_000;

describe("formatDownloadProgress", () => {
  it("joins percent, size, and speed", () => {
    expect(
      formatDownloadProgress({
        percent: 42,
        transferred: 83_886_080,
        total: 198_180_864,
        bytesPerSecond: 524_288,
      }),
    ).toBe("42% · 80 MB / 189 MB · 512 KB/s");
  });

  it("omits size and speed when unknown", () => {
    expect(
      formatDownloadProgress({
        percent: 10,
        transferred: 0,
        total: 0,
        bytesPerSecond: 0,
      }),
    ).toBe("10%");
  });
});

describe("formatBytesPerSecond", () => {
  it("returns null for non-positive rates", () => {
    expect(formatBytesPerSecond(0)).toBeNull();
    expect(formatBytesPerSecond(-1)).toBeNull();
  });

  it("appends /s to formatBytes", () => {
    expect(formatBytesPerSecond(1024)).toBe(`${formatBytes(1024)}/s`);
  });
});

describe("formatCost", () => {
  it("converts nano-CNY to ¥, rounded to fen", () => {
    expect(formatCost(120_000_000)).toBe("¥0.12");
    expect(formatCost(YUAN)).toBe("¥1.00");
  });

  it("shows「—」for zero / negative (无花销，不显 ¥0.00) — §7.5", () => {
    expect(formatCost(0)).toBe("—");
    expect(formatCost(-5)).toBe("—");
  });

  it("shows「<¥0.01」for a cost that rounds below one fen", () => {
    expect(formatCost(1000)).toBe("<¥0.01");
  });
});

describe("formatDisplayCost / pickCostMoney (BYOK ≈)", () => {
  it("prefixes ≈ only for estimates; billed stays plain ¥", () => {
    expect(formatDisplayCost(YUAN, false)).toBe("¥1.00");
    expect(formatDisplayCost(YUAN, true)).toBe("≈¥1.00");
    expect(formatDisplayCost(0, true)).toBe("—");
  });

  it("picks billed total over estimated_total", () => {
    expect(pickCostMoney({ total: 100, estimated_total: 999 })).toEqual({
      nano: 100,
      estimated: false,
      currency: "CNY",
    });
    expect(pickCostMoney({ total: 0, estimated_total: 999 })).toEqual({
      nano: 999,
      estimated: true,
      currency: "CNY",
    });
    expect(pickCostMoney({ total: 0 })).toEqual({
      nano: 0,
      estimated: false,
      currency: "CNY",
    });
  });

  it("treats total > 0 as billed (not estimate), even with pricing_source", () => {
    expect(pickCostMoney({ total: 100, pricing_source: "curated" })).toEqual({
      nano: 100,
      estimated: false,
      currency: "CNY",
    });
  });

  // 一个回合可以记账人民币、估算美元：估算走 estimated_currency，缺省才回落记账币种。
  // 不做汇率换算，所以币种必须随金额一起交给调用方，否则会拿 ¥ 的符号去印 $ 的数。
  it("carries the estimate's own currency, falling back to the billed one", () => {
    expect(
      pickCostMoney({
        total: 0,
        currency: "CNY",
        estimated_total: 999,
        estimated_currency: "USD",
      }),
    ).toEqual({ nano: 999, estimated: true, currency: "USD" });
    expect(
      pickCostMoney({ total: 0, currency: "USD", estimated_total: 999 }),
    ).toEqual({ nano: 999, estimated: true, currency: "USD" });
  });

  it("appends 自带密钥·估算 caption for estimates", () => {
    expect(formatCostCaption(YUAN, true)).toBe(`≈¥1.00 ${COST_ESTIMATE_LABEL}`);
    expect(formatCostCaption(YUAN, false)).toBe("¥1.00");
  });
});

describe("formatCompact", () => {
  it("keeps small counts verbatim, abbreviates k then M (用量大数)", () => {
    expect(formatCompact(0)).toBe("0");
    expect(formatCompact(820)).toBe("820");
    expect(formatCompact(8200)).toBe("8.2k");
    expect(formatCompact(820_000)).toBe("820.0k");
    expect(formatCompact(2_000_000)).toBe("2.0M");
  });
});

describe("formatMessageTimeOfDay", () => {
  it("returns HH:MM for a valid ISO timestamp", () => {
    expect(formatMessageTimeOfDay("2026-07-05T14:32:00")).toMatch(/14:32/);
  });
});

describe("formatDateDivider", () => {
  it("labels today, yesterday, same year, and cross-year", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-05T12:00:00"));

    expect(formatDateDivider("2026-07-05T08:00:00")).toBe("今天");
    expect(formatDateDivider("2026-07-04T08:00:00")).toBe("昨天");
    expect(formatDateDivider("2026-03-15T08:00:00")).toBe("3月15日");
    expect(formatDateDivider("2025-03-15T08:00:00")).toBe("2025年3月15日");

    vi.useRealTimers();
  });
});

describe("formatMessageTime", () => {
  it("adds day context for list previews", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-05T12:00:00"));

    expect(formatMessageTime("2026-07-05T08:30:00")).toMatch(/08:30/);
    expect(formatMessageTime("2026-07-04T08:30:00")).toBe("昨天 08:30");

    vi.useRealTimers();
  });
});

describe("formatDuration / formatDurationSec", () => {
  it("keeps sub-minute clocks as seconds", () => {
    expect(formatDuration(45_000)).toBe("45s");
    expect(formatDurationSec(45)).toBe("45s");
  });

  it("splits minutes and seconds with a space", () => {
    expect(formatDuration(88_000)).toBe("1m 28s");
    expect(formatDurationSec(968)).toBe("16m 8s");
  });

  it("drops seconds once past an hour", () => {
    expect(formatDuration(3_723_000)).toBe("1h 2m");
    expect(formatDurationSec(3_723)).toBe("1h 2m");
  });

  it("strips the face suffix for every compact shape", () => {
    expect(stripDurationFaceSuffix("思考中 · 45s")).toBe("思考中");
    expect(stripDurationFaceSuffix("思考中 · 16m 8s")).toBe("思考中");
    expect(stripDurationFaceSuffix("已完成 · 1h 2m")).toBe("已完成");
    expect(stripDurationFaceSuffix("已完成 · 1m 28s · 含质询")).toBe(
      "已完成 · 1m 28s · 含质询",
    );
  });
});

describe("chunksTailText / char token estimate", () => {
  it("matches tailText on a short joined stream", () => {
    const chunks = ["hello ", "world"];
    expect(chunksTailText(chunks)).toBe(tailText(chunks.join("")));
  });

  it("returns the trailing window for a long chunk list", () => {
    const chunks = ["x".repeat(100), "y".repeat(100), "TAIL_MARKER"];
    const preview = chunksTailText(chunks, 80);
    expect(preview).toContain("TAIL_MARKER");
    expect(preview.length).toBeLessThanOrEqual(81);
  });

  it("sums chunk chars and coarse-estimates tokens", () => {
    expect(sumChunkChars(["ab", "cde"])).toBe(5);
    expect(estimateTokensFromCharCount(0)).toBe(0);
    expect(estimateTokensFromCharCount(5)).toBe(3);
  });
});
