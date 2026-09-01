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
 * 对话里任务/节点/工具/状态条的秒表都走这里；单位间留空格（GitHub Actions / Linear / Vercel 同形）。
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

/** 从状态行去掉末尾用时。a11y 完成态改由 `durationText` 报；进行中不把每秒跳动读进 aria。 */
export function stripDurationFaceSuffix(text: string): string {
  return text.replace(/ · \d+(?:h \d+m|m \d+s|s)$/, "");
}

/** 1 单位 = 10^9 nano：台账/接口里钱的规范单位（整数，绝不用 float）。 */
const NANO_PER_UNIT = 1_000_000_000;

/**
 * 币种符号表。后端每个金额都自带 `currency`（平台记账 CNY / BYOK 社区估算 USD），
 * **全系统无汇率换算**——这里只挑符号，绝不折算。
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
 * BYOK 估算金额的轻量说明（tooltip / title）——与平台记账视觉分离，
 * 明确「非上游账单」。
 */
export const COST_ESTIMATE_HINT = "按社区价目（美元列表价）估算，非上游账单";

/** 费用位标注：自带密钥场景（credential_source=user / estimated_total）。 */
export const COST_ESTIMATE_LABEL = "自带密钥·估算";

/**
 * 费用位标注：BYOK 且两层价卡全落空（`pricing_source=unpriced`）。
 * 有真实花费但平台无价可算——显式标注，不得以「省略费用段」暗示免费
 * （拍板 2026-07-20：未计价运行显式标识，金额位仍显「—」绝不冒充数字）。
 */
export const COST_UNPRICED_LABEL = "自带密钥·未计价";

/** 未计价标注的轻量说明（tooltip / title）。 */
export const COST_UNPRICED_HINT =
  "平台无此模型价目（社区价目缺），实际费用以上游供应商账单为准";

/**
 * 把整数 nano 成本格式化为带币种符号的展示串（大众面，§7.2）。
 *
 * 钱一律以整数 nano 流转（1 单位 = 1e9），绝不用 float；前端直接 `nano/1e9`，
 * **不做汇率换算**——符号取自后端随金额下发的 `currency`（平台记账 CNY、BYOK
 * 社区估算 USD）。约定（§7.5）：0 / 无花销显「—」（不显「¥0.00」）；有花销但
 * 不足 1 分/1 美分显「<¥0.01」/「<$0.01」。
 */
export function formatCost(nano: number, currency?: string | null): string {
  if (nano <= 0) return "—";
  const symbol = currencySymbol(currency);
  const amount = nano / NANO_PER_UNIT;
  if (amount < 0.01) return `<${symbol}0.01`;
  return `${symbol}${amount.toFixed(2)}`;
}

/**
 * 展示金额：平台记账走 {@link formatCost}；估算金额一律带「≈」前缀，
 * 不得与记账金额混淆。0 / 无值仍显「—」（`pricing_source=unpriced` 同此）。
 */
export function formatDisplayCost(
  nano: number,
  estimated = false,
  currency?: string | null,
): string {
  const base = formatCost(nano, currency);
  if (base === "—" || !estimated) return base;
  return `≈${base}`;
}

/**
 * SSE / fold `CostBreakdown` 叶子上挑「记账 total vs 估算 estimated_total」，
 * **连同该笔金额自己的币种**一起返回。
 *
 * 记账 total 用 `currency`；BYOK 估算用 `estimated_currency`（一个回合可能记账
 * 人民币、估算美元），缺省回落 `currency` 兼容旧 wire。调用方必须把 currency
 * 一路带到格式化，禁止按 `pricing_source` 猜币种。
 */
export function pickCostMoney(
  cost:
    | {
        total: number;
        currency?: string | null;
        estimated_total?: number | null;
        estimated_currency?: string | null;
        pricing_source?: string | null;
      }
    | null
    | undefined,
): { nano: number; estimated: boolean; currency: string } | null {
  if (!cost) return null;
  const billedCurrency = cost.currency || DEFAULT_CURRENCY;
  if (cost.total > 0) {
    return { nano: cost.total, estimated: false, currency: billedCurrency };
  }
  const est = cost.estimated_total;
  if (est != null && est > 0) {
    return {
      nano: est,
      estimated: true,
      currency: cost.estimated_currency || billedCurrency,
    };
  }
  return { nano: 0, estimated: false, currency: billedCurrency };
}

/** 费用展示串：有金额时附带「自带密钥·估算」标注。 */
export function formatCostCaption(
  nano: number,
  estimated = false,
  currency?: string | null,
): string {
  const base = formatDisplayCost(nano, estimated, currency);
  if (base === "—" || !estimated) return base;
  return `${base} ${COST_ESTIMATE_LABEL}`;
}
