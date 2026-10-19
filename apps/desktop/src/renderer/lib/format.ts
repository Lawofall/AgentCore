/** 把字节数格式化为人类可读字符串。 */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[i]}`;
}

/** 下载吞吐：`formatBytes(n) + "/s"`（n≤0 时返回 null，调用方自行省略）。 */
export function formatBytesPerSecond(bytesPerSecond: number): string | null {
  if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return null;
  return `${formatBytes(bytesPerSecond)}/s`;
}

/**
 * 更新下载进度摘要（百分比 + 已传/总量 + 速度）。
 * `total`/`bytesPerSecond` 缺失时自动省略对应片段。
 */
export function formatDownloadProgress(opts: {
  percent: number;
  transferred: number;
  total: number;
  bytesPerSecond: number;
}): string {
  const parts: string[] = [`${opts.percent}%`];
  if (opts.total > 0) {
    parts.push(`${formatBytes(opts.transferred)} / ${formatBytes(opts.total)}`);
  }
  const speed = formatBytesPerSecond(opts.bytesPerSecond);
  if (speed) parts.push(speed);
  return parts.join(" · ");
}

const CJK_RANGE = /[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uff00-\uffef]/;

/**
 * 粗估文本的 token 数，用于流式进度展示（非计费用途）。
 *
 * 真实 token 数只有 LLM 网关在回合结束时给出（usage）；流式过程中每个
 * delta 不带 token，因此这里用「CJK 约 1 token/字，其余约 4 字/token」的
 * 经验启发式给出一个量级感知，足够驱动节点上的实时进度。
 */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  let cjk = 0;
  let other = 0;
  for (const ch of text) {
    if (CJK_RANGE.test(ch)) cjk++;
    else other++;
  }
  return Math.ceil(cjk + other / 4);
}

/** Sum chunk string lengths without joining (流式 face token 粗估输入). */
export function sumChunkChars(chunks: readonly string[]): number {
  let n = 0;
  for (const c of chunks) n += c.length;
  return n;
}

/**
 * Coarse token estimate from total character count — avoids scanning full text
 * on every stream delta (CJK/latin mix ≈ 0.5 token/char mid).
 */
export function estimateTokensFromCharCount(chars: number): number {
  if (chars <= 0) return 0;
  return Math.ceil(chars / 2);
}

/** 紧凑数字：1234 → "1.2k"、2_000_000 → "2.0M"（用于 token 等大数展示）。 */
export function formatCompact(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

/** 账户用量入/出构成：`↓ 6.2k ↑ 4.7k`，有命中率才跟 `· 命中 48%`。 */
export function formatTokenFlow(
  input: number,
  output: number,
  hitRatePercent?: number | null,
): string {
  const body = `↓ ${formatCompact(input)} ↑ ${formatCompact(output)}`;
  if (hitRatePercent == null || hitRatePercent <= 0) return body;
  return `${body} · 命中 ${hitRatePercent}%`;
}

/** 用量详情计数：1_234 → "1,234"；≥1_000_000 仍走 {@link formatCompact}。 */
export function formatUsageCount(n: number): string {
  if (!Number.isFinite(n)) return "0";
  const v = Math.round(n);
  const abs = Math.abs(v);
  if (abs >= 1_000_000) {
    const compact = formatCompact(abs);
    return v < 0 ? `-${compact}` : compact;
  }
  const body = String(abs).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return v < 0 ? `-${body}` : body;
}

/** Hide thresholds for 输出速度 in「用量」— short bursts and tiny replies are noise. */
export const OUTPUT_SPEED_MIN_TOKENS = 10;
export const OUTPUT_SPEED_MIN_MS = 250;

/**
 * Decode throughput for the assistant「用量」popover, nested under 输出.
 * `outputTokens / (generationMs/1000)` — decode window only, not whole-turn 用时.
 * Value is `42.0 tokens/s` (one decimal). Returns null when the number would
 * lie or jitter (old rows, sidecar token-empty).
 */
export function formatOutputSpeed(
  outputTokens: number,
  generationMs: number,
): string | null {
  if (
    !Number.isFinite(outputTokens) ||
    !Number.isFinite(generationMs) ||
    outputTokens < OUTPUT_SPEED_MIN_TOKENS ||
    generationMs < OUTPUT_SPEED_MIN_MS
  ) {
    return null;
  }
  const tps = outputTokens / (generationMs / 1000);
  if (!Number.isFinite(tps) || tps <= 0) return null;
  return `${tps.toFixed(1)} tokens/s`;
}

/**
 * 用量面板 TTFT。不足 1 分钟保留一位小数秒（`1.8s`）；更长走 {@link formatDuration}。
 * 四舍五入后仍不到 0.1s 写 `<0.1s`。非正数不展示。
 */
export function formatTtft(ms: number): string | null {
  if (!Number.isFinite(ms) || ms <= 0) return null;
  if (ms >= 60_000) return formatDuration(ms);
  const text = (ms / 1000).toFixed(1);
  if (text === "0.0") return "<0.1s";
  return `${text}s`;
}

/** 取文本末尾若干字符并折行成单段预览（用于 worker 节点的实时输出片段：运行中
 * 最新内容在末尾，tail 才是「正在写什么」）。 */
export function tailText(text: string, max = 80): string {
  const flat = text.replace(/\s+/g, " ").trim();
  if (flat.length <= max) return flat;
  return `…${flat.slice(flat.length - max)}`;
}

/**
 * Tail preview from chunk list without `join("")` of the full stream.
 * Accumulates from the end until enough raw chars for {@link tailText}.
 */
export function chunksTailText(chunks: readonly string[], max = 80): string {
  if (chunks.length === 0) return "";
  // Whitespace collapse can shrink; keep slack so the flattened tail is full.
  const need = max + 48;
  let len = 0;
  let start = chunks.length;
  while (start > 0 && len < need) {
    start -= 1;
    len += chunks[start]?.length ?? 0;
  }
  let raw =
    start === 0 && len <= need ? chunks.join("") : chunks.slice(start).join("");
  if (raw.length > need) raw = raw.slice(raw.length - need);
  return tailText(raw, max);
}

/** 取文本开头若干字符并折行成单段预览（用于 CEO 汇总节点：成稿答案的开头通常即
 * 结论/主旨，比取末尾片段更能代表内容，避免长答案截出半句结尾乱码）。 */
export function headText(text: string, max = 80): string {
  const flat = text.replace(/\s+/g, " ").trim();
  if (flat.length <= max) return flat;
  return `${flat.slice(0, max)}…`;
}

function sameCalendarDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/** 相对时间：「刚刚」/「N 分钟前」/「N 小时前」/「N 天前」。非法输入返回空串。 */
export function timeAgo(date: string | Date): string {
  const t = new Date(date).getTime();
  if (Number.isNaN(t)) return "";
  const ms = Date.now() - t;
  const min = Math.floor(ms / 60_000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const d = Math.floor(hr / 24);
  return `${d} 天前`;
}

/** 消息时刻 "HH:MM"（线程内日期由分隔条承担）。非法输入返回空串。 */
export function formatMessageTimeOfDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * IM 线程日期分隔条：今天 / 昨天 / M月D日 / YYYY年M月D日。非法输入返回空串。
 */
export function formatDateDivider(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  if (sameCalendarDay(d, now)) return "今天";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameCalendarDay(d, yesterday)) return "昨天";
  const md = `${d.getMonth() + 1}月${d.getDate()}日`;
  if (d.getFullYear() === now.getFullYear()) return md;
  return `${d.getFullYear()}年${md}`;
}

/**
 * 消息时间戳展示串（侧栏预览等无日期上下文处）：今天显 "HH:MM"，昨天 "昨天 HH:MM"，
 * 同年 "M月D日 HH:MM"，跨年 "YYYY年M月D日 HH:MM"。非法输入返回空串。
 */
export function formatMessageTime(iso: string): string {
  const tod = formatMessageTimeOfDay(iso);
  if (!tod) return "";
  const d = new Date(iso);
  const now = new Date();
  if (sameCalendarDay(d, now)) return tod;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameCalendarDay(d, yesterday)) return `昨天 ${tod}`;
  const md = `${d.getMonth() + 1}月${d.getDate()}日`;
  if (d.getFullYear() === now.getFullYear()) return `${md} ${tod}`;
  return `${d.getFullYear()}年${md} ${tod}`;
}

/**
 * 毫秒时长 → 紧凑用时："45s" / "2m 34s" / "1h 2m"。
 * 节点 face / 状态条 / 气泡脚的用时都走这里；单位间留空格（GitHub Actions / Linear / Vercel 同形）。
 * 过小时不再带秒——长跑密度优先。倒计时、中文时限、录音 `m:ss` 不走本函数。
 */
export function formatDuration(ms: number): string {
  const totalSec = Math.round(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${s}s`;
}

/** 整数秒 → {@link formatDuration}。进行中秒表已有秒数时用这个，禁止再拼裸秒。 */
export function formatDurationSec(totalSec: number): string {
  if (!Number.isFinite(totalSec) || totalSec < 0) return "0s";
  return formatDuration(totalSec * 1000);
}

/** 进行中秒表文案；满 1 秒才出（避免闪 `0s`）。 */
export function formatLiveElapsed(
  elapsedSec: number | null | undefined,
): string | null {
  if (elapsedSec == null || elapsedSec < 1) return null;
  return formatDurationSec(elapsedSec);
}

/** 从状态行去掉末尾用时。a11y 完成态改由 `durationText` 报；进行中不把每秒跳动读进 aria。 */
export function stripDurationFaceSuffix(text: string): string {
  return text.replace(/ · \d+(?:h \d+m|m \d+s|s)$/, "");
}

/** 1 单位 = 10^9 nano：台账/接口里钱的规范单位（整数，绝不用 float）。 */
const NANO_PER_UNIT = 1_000_000_000;

/**
 * 币种符号表。后端每个金额都自带 `currency`（产品名义价 CNY；遗留行偶发 USD），
 * **展示层无 live 汇率**——这里只挑符号，绝不折算。
 */
const CURRENCY_SYMBOLS: Record<string, string> = { CNY: "¥", USD: "$" };

/** 默认币种：平台记账台账恒为人民币；仅当后端没给 `currency` 时兜底。 */
const DEFAULT_CURRENCY = "CNY";

/** 币种代码 → 展示符号；未知币种退化为「CODE 」前缀，不冒充 ¥。 */
function currencySymbol(currency?: string | null): string {
  const code = (currency || DEFAULT_CURRENCY).toUpperCase();
  return CURRENCY_SYMBOLS[code] ?? `${code} `;
}

/**
 * BYOK 费用位说明（详情 / tooltip）：官网价目，人民币，不扣平台积分。
 */
export const COST_ESTIMATE_HINT = "按官网价目";

/** 平台代付写积分；自己的 Key / 遗留 USD 写现金符号。 */
export type CostUnit = "credit" | "cash";

export type CostMoney = {
  nano: number;
  /** True only for leftover USD community estimates (≈$). */
  estimated: boolean;
  currency: string;
  unit: CostUnit;
};

export type SplitCostMoney = {
  billed: CostMoney | null;
  extra: CostMoney | null;
};

function isCreditCurrency(currency?: string | null): boolean {
  return (currency || DEFAULT_CURRENCY).toUpperCase() === "CNY";
}

function isUsd(currency?: string | null): boolean {
  return (currency || "").toUpperCase() === "USD";
}

function formatCreditAmount(amount: number): string {
  if (amount < 0.01) return "<0.01 积分";
  return `${amount.toFixed(2)} 积分`;
}

function formatCashAmount(amount: number, currency?: string | null): string {
  const symbol = currencySymbol(currency);
  if (amount < 0.01) return `<${symbol}0.01`;
  return `${symbol}${amount.toFixed(2)}`;
}

function resolveCostUnit(currency?: string | null, unit?: CostUnit): CostUnit {
  if (unit) return unit;
  return isCreditCurrency(currency) ? "credit" : "cash";
}

function costMoney(
  nano: number,
  currency: string,
  unit: CostUnit,
  estimated = false,
): CostMoney | null {
  if (nano <= 0) return null;
  return { nano, estimated, currency, unit };
}

/**
 * 把整数 nano 成本格式化为展示串（大众面，§7.2）。
 *
 * 钱一律以整数 nano 流转（1 单位 = 1e9），绝不用 float；前端直接 `nano/1e9`，
 * **不做汇率换算**。平台 CNY = 积分（1 积分 = ¥1 卖价）；自己的 Key 同额写 ¥；
 * 遗留 USD 仍 `$`。约定（§7.5）：0 / 无花销显「—」（不显「0.00 积分」）；
 * 有花销但不足 0.01 显「<0.01 积分」/「<¥0.01」/「<$0.01」。
 */
export function formatCost(
  nano: number,
  currency?: string | null,
  unit?: CostUnit,
): string {
  if (nano <= 0) return "—";
  const amount = nano / NANO_PER_UNIT;
  const resolved = resolveCostUnit(currency, unit);
  if (resolved === "credit" && isCreditCurrency(currency)) {
    return formatCreditAmount(amount);
  }
  return formatCashAmount(amount, currency);
}

/**
 * 积分还剩（用量额度行）。0 显「0.00 积分」（用尽），不足 0.01 显「<0.01 积分」。
 * 与 {@link formatCost} 的「0 = 无花销 → —」不同，避免把用尽读成没花钱。
 */
export function formatQuotaRemaining(
  nano: number,
  currency?: string | null,
): string {
  if (!Number.isFinite(nano) || nano <= 0) {
    return isCreditCurrency(currency)
      ? "0.00 积分"
      : `${currencySymbol(currency)}0.00`;
  }
  return formatCost(nano, currency);
}

/**
 * 展示金额：平台积分走 {@link formatCost}；自己的 Key 写 ¥；仅遗留美元估算带「≈」。
 * 0 / 无值仍显「—」。
 */
export function formatDisplayCost(
  nano: number,
  estimated = false,
  currency?: string | null,
  unit?: CostUnit,
): string {
  const base = formatCost(nano, currency, unit);
  if (base === "—" || !estimated) return base;
  return `≈${base}`;
}

export function formatCostMoney(money: CostMoney): string {
  return formatDisplayCost(
    money.nano,
    money.estimated,
    money.currency,
    money.unit,
  );
}

/** 平台积分在前、自己的 Key ¥ 在后；混合一轮写成 `0.01 积分 · ¥0.04`。 */
export function formatCostBill(
  billed: CostMoney | null | undefined,
  extra?: CostMoney | null,
  provisional = false,
): string | null {
  const parts: string[] = [];
  if (billed && billed.nano > 0) parts.push(formatCostMoney(billed));
  if (extra && extra.nano > 0) parts.push(formatCostMoney(extra));
  if (parts.length === 0) return null;
  const text = parts.join(" · ");
  return provisional ? `${text} 至今` : text;
}

export function mergeCostMoney(
  left: CostMoney | null | undefined,
  right: CostMoney | null | undefined,
): CostMoney | null {
  if (!left || left.nano <= 0) {
    return right && right.nano > 0 ? right : null;
  }
  if (!right || right.nano <= 0) return left;
  if (
    left.currency !== right.currency ||
    left.estimated !== right.estimated ||
    left.unit !== right.unit
  ) {
    return left;
  }
  return { ...left, nano: left.nano + right.nano };
}

export function mergeSplitCost(
  left: SplitCostMoney,
  right: SplitCostMoney,
): SplitCostMoney {
  return {
    billed: mergeCostMoney(left.billed, right.billed),
    extra: mergeCostMoney(left.extra, right.extra),
  };
}

export function higherCostMoney(
  left: CostMoney | null | undefined,
  right: CostMoney | null | undefined,
): CostMoney | null {
  if (!left || left.nano <= 0) {
    return right && right.nano > 0 ? right : null;
  }
  if (!right || right.nano <= 0) return left;
  if (
    left.currency !== right.currency ||
    left.estimated !== right.estimated ||
    left.unit !== right.unit
  ) {
    return left;
  }
  return left.nano >= right.nano ? left : right;
}

export function higherSplitCost(
  left: SplitCostMoney,
  right: SplitCostMoney,
): SplitCostMoney {
  return {
    billed: higherCostMoney(left.billed, right.billed),
    extra: higherCostMoney(left.extra, right.extra),
  };
}

type CostLeafLike = {
  total: number;
  currency?: string | null;
  estimated_total?: number | null;
  estimated_currency?: string | null;
  pricing_source?: string | null;
};

function extraMoney(nano: number, currency: string): CostMoney | null {
  return costMoney(nano, currency, "cash", isUsd(currency));
}

/**
 * 回合 / run 叶子：`total` 已含 BYOK 名义价（与 `estimated_total` 同币可加）。
 * 拆开后平台列写积分、自己的 Key 写 ¥。
 */
export function splitCostMoney(
  cost: CostLeafLike | null | undefined,
): SplitCostMoney {
  if (!cost) return { billed: null, extra: null };
  const billedCurrency = cost.currency || DEFAULT_CURRENCY;
  const extraCurrency = cost.estimated_currency || billedCurrency;
  const total = cost.total ?? 0;
  const extra = cost.estimated_total ?? 0;
  const sameCurrency =
    billedCurrency.toUpperCase() === extraCurrency.toUpperCase();
  let billedNano = 0;
  if (total > 0) {
    if (sameCurrency && extra > 0 && total >= extra) {
      billedNano = total - extra;
    } else {
      billedNano = total;
    }
  }
  return {
    billed: costMoney(billedNano, billedCurrency, "credit"),
    extra: extra > 0 ? extraMoney(extra, extraCurrency) : null,
  };
}

/**
 * 账户用量窗：`cost.total` 只含平台记账，`estimated_cost.total` 是 BYOK 切片，
 * 两列互斥，不能相减。
 */
export function splitAccountSpend(opts: {
  billed: number;
  billedCurrency?: string | null;
  extra: number;
  extraCurrency?: string | null;
}): SplitCostMoney {
  const billedCurrency = opts.billedCurrency || DEFAULT_CURRENCY;
  const extraCurrency = opts.extraCurrency || billedCurrency;
  return {
    billed: costMoney(opts.billed, billedCurrency, "credit"),
    extra: extraMoney(opts.extra, extraCurrency),
  };
}

export function sumSplitCosts(
  costs: Array<CostLeafLike | null | undefined>,
): SplitCostMoney {
  let acc: SplitCostMoney = { billed: null, extra: null };
  for (const cost of costs) {
    acc = mergeSplitCost(acc, splitCostMoney(cost));
  }
  return acc;
}

/**
 * 把 nano 映到与 {@link formatCost} 相同的「分」整数（`toFixed(2)` 口径）。
 * 0 / 不足 0.01 返回 null——那些走「—」/「<0.01 积分」，不能拿来做加减找齐。
 */
function displayFen(nano: number): number | null {
  if (nano <= 0) return null;
  const amount = nano / NANO_PER_UNIT;
  if (amount < 0.01) return null;
  return Math.round(Number(amount.toFixed(2)) * 100);
}

function formatFen(
  fen: number,
  currency?: string | null,
  unit?: CostUnit,
): string {
  const amount = (fen / 100).toFixed(2);
  const resolved = resolveCostUnit(currency, unit);
  if (resolved === "credit" && isCreditCurrency(currency)) {
    return `${amount} 积分`;
  }
  return `${currencySymbol(currency)}${amount}`;
}

/**
 * 成本构成（`total == input + output`）的展示找齐：分项各自 `toFixed(2)` 会
 * 和总额差 1 分。输入先按展示口径取整，输出吃差额，使看见的输入+输出 = 总额。
 * 任一分项不足 1 分时不强找齐，退回 {@link formatCost}。
 */
export function formatAlignedCostParts(
  inputNano: number,
  outputNano: number,
  totalNano: number,
  currency?: string | null,
  unit?: CostUnit,
): { input: string; output: string } {
  const totalFen = displayFen(totalNano);
  const inputFenRaw = displayFen(inputNano);
  if (
    totalFen == null ||
    (inputNano > 0 && inputFenRaw == null) ||
    (outputNano > 0 && displayFen(outputNano) == null)
  ) {
    return {
      input: formatCost(inputNano, currency, unit),
      output: formatCost(outputNano, currency, unit),
    };
  }
  if (inputNano <= 0) {
    return { input: "—", output: formatFen(totalFen, currency, unit) };
  }
  if (outputNano <= 0) {
    return { input: formatFen(totalFen, currency, unit), output: "—" };
  }
  let inputFen = inputFenRaw ?? 0;
  let outputFen = totalFen - inputFen;
  if (outputFen < 0) {
    outputFen = displayFen(outputNano) ?? 0;
    inputFen = totalFen - outputFen;
  }
  return {
    input: formatFen(inputFen, currency, unit),
    output: formatFen(outputFen, currency, unit),
  };
}

/**
 * SSE / fold `CostBreakdown` 叶子上挑一笔展示金额（签名 / 单值回落）。
 * 混合一轮请用 {@link splitCostMoney}，不要把积分和 ¥ 加成一个数。
 */
export function pickCostMoney(
  cost: CostLeafLike | null | undefined,
): CostMoney | null {
  const split = splitCostMoney(cost);
  return split.billed ?? split.extra;
}
