import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Tailwind-aware className merge (last conflicting utility wins). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/**
 * 币种符号表。后端每笔金额都自带 `currency`（平台记账走 curated 人民币价卡 = CNY，
 * BYOK 估算走社区价目快照 = USD），**全系统无汇率换算**——这里只挑符号，绝不折算。
 * 与桌面 `renderer/lib/format.ts::formatCost` 同口径。
 */
const CURRENCY_SYMBOLS: Record<string, string> = { CNY: "¥", USD: "$" };

/** 后端 `CostBreakdown.currency` 缺省值；仅在接口没给币种时兜底。 */
export const DEFAULT_CURRENCY = "CNY";

/** 币种代码 → 展示符号；未知币种退化为「CODE 」前缀，不冒充 ¥。 */
export function currencySymbol(currency?: string | null): string {
  const code = (currency || DEFAULT_CURRENCY).toUpperCase();
  return CURRENCY_SYMBOLS[code] ?? `${code} `;
}

/**
 * 金额分位：与 {@link fmtInt} 同一套 zh-CN 分组，两分位定长。控制台的金额和计数常
 * 同屏（总览首屏「今日成本」紧挨「今日活跃用户」），只有计数带千分位时「¥1234567.89」
 * 会被看错量级。桌面 `renderer/lib/format.ts::formatCost` 是逐回合的几分几元，不分组；
 * 除分组外（符号来源、两位小数、<0.01 下限）两边仍同口径。
 */
const MONEY = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/**
 * Money in the amount's **own** major unit, with the symbol taken from the
 * backend-stamped `currency` — never guessed from `billing_mode`, never converted.
 * Tiny non-zero spend floors to "<¥0.01" so a real-but-rounding-to-zero cost never
 * reads as free; a known zero stays "¥0.00".
 */
export function fmtMoney(major: number, currency?: string | null): string {
  const symbol = currencySymbol(currency);
  if (major > 0 && major < 0.01) return `<${symbol}0.01`;
  return `${symbol}${MONEY.format(major)}`;
}

/**
 * 仅用于口径上恒为人民币的金额（如后台配置的配额上限）。凡是随接口下发
 * `currency` 的金额一律用 {@link fmtMoney}，否则 BYOK 的美元估算会被标成 ¥。
 */
export function fmtCny(yuan: number): string {
  return fmtMoney(yuan, DEFAULT_CURRENCY);
}

/** BYOK estimate caption — always ≈-prefixed; 0 →「—」. */
export const COST_ESTIMATE_HINT =
  "按社区价目估算，非上游账单";

/** 估算金额（≈ 前缀 + 自带币种）；0 →「—」，不显「≈¥0.00」。 */
export function fmtEstimatedMoney(
  major: number,
  currency?: string | null,
): string {
  if (major <= 0) return "—";
  return `≈${fmtMoney(major, currency)}`;
}

export function fmtEstimatedCny(yuan: number): string {
  return fmtEstimatedMoney(yuan, DEFAULT_CURRENCY);
}

const COMPACT = new Intl.NumberFormat("zh-CN", {
  notation: "compact",
  maximumFractionDigits: 1,
});

/** Compact formatting for large counts (e.g. token totals): 2100000 → "210万". */
export function fmtCompact(n: number): string {
  return COMPACT.format(n);
}

/** Plain integer with thousands separators. */
export function fmtInt(n: number): string {
  return n.toLocaleString("zh-CN");
}

/**
 * A total that may not be known yet: `known === false` renders「—」instead of a number.
 *
 * A count sourced from a `useState(0)` reads as a fact even while the request is still
 * in flight or has just failed —「共 0 个账号」over a skeleton or next to a red error
 * line is the same screenshot an operator files as "all the users are gone".
 */
export function fmtCount(total: number, known: boolean): string {
  return known ? fmtInt(total) : "—";
}

/** Milliseconds, compacted: <1s stays "840ms", ≥1s becomes "2.4s". */
export function fmtMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/**
 * 看板时间口径：后端的「今日 / 本月 / 近 7 日」窗口与趋势分桶**一律按 UTC 日切**
 * （`overview.py` / `usage.py` / `observability.py`，MVP 取舍，前端不做换算）。
 * 同一屏里的日期轴与时间列因此都必须走 UTC，否则两处的「08-13」不是同一天。
 */
export const UTC_WINDOW_HINT =
  "后端按 UTC 日切统计，本页日期与时间均为 UTC，可能与本地日期相差一天";

/** ISO date "2026-06-18" → "06-18"（已是 UTC 日，后端趋势即按 UTC 分桶）。 */
export function mmdd(iso: string): string {
  const [, m, d] = iso.split("-");
  return m && d ? `${m}-${d}` : iso;
}

/**
 * ISO timestamp → "MM-DD HH:mm" in **UTC** — 与看板窗口 / 趋势轴同口径。
 * 与 UTC 统计同屏的时间列用它，别用 {@link fmtTime}。
 */
export function fmtTimeUtc(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

/**
 * ISO timestamp → "MM-DD HH:mm" in the viewer's local zone (compact log axis).
 * 只用于本身没有 UTC 窗口作参照的时间列（会话 / 通知）；与 UTC 统计同屏时用
 * {@link fmtTimeUtc}。
 */
export function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 1 单位 = 10^9 nano：台账规范单位（整数，绝不用 float）。 */
const NANO_PER_MAJOR = 1_000_000_000;

/**
 * Integer nano → 该金额自己币种的主单位（元 / dollar）. Used for raw-nano fields
 * (trend / per-user / per-model rows) that don't ship a pre-computed `cny_total`
 * like the window breakdowns do. 只换单位，不换币种。
 */
export function nanoToMajor(nano: number): number {
  return nano / NANO_PER_MAJOR;
}

/** @see nanoToMajor —— 旧名（主单位取决于该金额的 `currency`，并非恒为元）。 */
export function nanoToYuan(nano: number): number {
  return nanoToMajor(nano);
}

/** nano → 带币种符号的展示串；0 / 无花销显「—」，`estimated` 加 ≈ 前缀。 */
export function fmtNanoMoney(
  nano: number,
  currency?: string | null,
  estimated = false,
): string {
  if (nano <= 0) return "—";
  const body = fmtMoney(nanoToMajor(nano), currency);
  return estimated ? `≈${body}` : body;
}

/** 恒为人民币的 nano 金额；随接口下发币种的用 {@link fmtNanoMoney}。 */
export function fmtNanoCny(nano: number, estimated = false): string {
  return fmtNanoMoney(nano, DEFAULT_CURRENCY, estimated);
}

/**
 * Ledger `cost_events.role` → 大众-facing zh label, mirroring the desktop/mobile
 * 工资单 so an operator reads「视觉读图」not raw「vision」. Unknown roles fall back
 * to the raw string. `vision` tags a board_read 读图 sub-call to a separate vision
 * model (AI协作白板.md §九.4); `title`/`memory` are off-turn background calls.
 */
const ROLE_LABELS: Record<string, string> = {
  captain: "CEO",
  member: "队员",
  arena: "辩论",
  title: "标题生成",
  memory: "记忆整理",
  vision: "视觉读图",
};

/** A ledger role's zh label; unknown roles fall back to the raw string. */
export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

/** Number of `--agent-N` identity tokens defined in styles/globals.css. */
const AGENT_PALETTE_SIZE = 8;

/**
 * Deterministic FNV-1a string hash, so a given role always maps to the same
 * identity slot across reloads — never `Math.random` / insertion order, which
 * would reshuffle colors. Mirrors the desktop renderer's `agentIdentity` (each
 * frontend reimplements presentation helpers, no shared business logic).
 */
function hashRole(role: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < role.length; i++) {
    h ^= role.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/**
 * The role's identity color as a CSS `var(--agent-N)` reference (color-tokens.mdc
 * 角色身份色 — use inline; these are semantic OKLCH tokens, not ad-hoc colors).
 * Blank role falls back to slot 1.
 */
export function agentColorVar(role: string): string {
  const key = role.trim();
  const idx = key ? (hashRole(key) % AGENT_PALETTE_SIZE) + 1 : 1;
  return `var(--agent-${idx})`;
}
