/**
 * 手册用户可见文案 · 退役词黑名单。
 *
 * 口径源：`docs/01-产品/术语表.md`「命名约定」节（弃用 / 收敛项）。
 * 本表是门禁用的精简提取，不是术语表全文复刻。
 */

export interface RetiredTerm {
  /** 命中子串（字面） */
  term: string;
  /** 术语表口径摘要 */
  reason: string;
}

/**
 * 黑名单（用户可见手册文案不应再出现）。
 * 「成员」单独处理见 {@link isExemptMemberUsage}——合法所指（子成员 / 群成员等）豁免。
 */
export const RETIRED_TERMS: RetiredTerm[] = [
  {
    term: "组员",
    reason: "worker 中文展示一律「队员」，弃用「组员」",
  },
  {
    term: "批准",
    reason: "门动作用「放行」、按钮用「允许」，弃用「批准」作放行同义词",
  },
  {
    term: "中止",
    reason: "动作/状态用「停止 / 已停止」，弃用「中止」",
  },
  {
    term: "重跑",
    reason: "整轮重答用「重新生成」等，弃用游离「重跑」",
  },
  {
    term: "凭证",
    reason: "credentials 中文用「凭据」，弃用「凭证」",
  },
  {
    term: "聊天气泡",
    reason: "AI 消息气泡用「对话气泡」，弃用「聊天气泡」",
  },
  {
    term: "队长",
    reason: "弃用「队长」作 CEO / captain gloss",
  },
  {
    term: "协调官",
    reason: "弃用「协调官」作 CEO gloss",
  },
  {
    term: "会话列表",
    reason: "AI 对话实体用「对话」，弃用「会话列表」作其 gloss",
  },
  {
    term: "旧会话",
    reason: "AI 对话实体用「旧对话」，弃用「旧会话」",
  },
  {
    term: "上次会话",
    reason: "AI 对话实体用「上次对话」，弃用「上次会话」",
  },
  {
    term: "作废",
    reason: "orphaned 展示用「已失效」，弃用「作废」",
  },
  {
    term: "思考档位",
    reason: "reasoning effort 用「思考强度档」，弃用「思考档位」",
  },
  {
    term: "热修",
    reason: "产品词用「带现场续派 / 同人接续」；口语旧称仅允许定义性提及",
  },
  {
    term: "成员",
    reason: "指被委派 worker 时用「队员」；合法 Team/群/子成员见豁免",
  },
];

/**
 * 定义性提及豁免：退役词出现在引号内，且近邻出现「旧称 / 口语 / 弃用…」等元叙述。
 * 例：「带现场续派（口语有时叫「热修」）」。
 */
const DEFINITIONAL_NEAR =
  /(?:口语|也叫|旧称|曾称|原称|弃用|不用|勿用|不再说|不要说|勿称)/;

function windowAround(text: string, index: number, radius = 24): string {
  const start = Math.max(0, index - radius);
  const end = Math.min(text.length, index + radius);
  return text.slice(start, end);
}

/** 引号包裹的定义性提及（「词」/『词』/"词"）。 */
export function isDefinitionalMention(
  text: string,
  term: string,
  index: number,
): boolean {
  const before = text.slice(Math.max(0, index - 1), index);
  const after = text.slice(index + term.length, index + term.length + 1);
  const quoted =
    (before === "「" && after === "」") ||
    (before === "『" && after === "』") ||
    (before === '"' && after === '"') ||
    (before === "“" && after === "”");
  if (!quoted) return false;
  return DEFINITIONAL_NEAR.test(windowAround(text, index, 36));
}

/**
 * 「成员」合法所指：子成员、群成员、Team 成员、结构成员、邀请成员（协作桌名册）等非 worker gloss。
 * 裸「成员」指 worker 时不豁免。
 */
export function isExemptMemberUsage(text: string, index: number): boolean {
  const prefix = text.slice(Math.max(0, index - 4), index);
  if (/(?:子|群|IM|Team|团队结构|结构|邀请)$/.test(prefix)) return true;
  // 「captain + 成员」类结构说明
  const around = windowAround(text, index, 12);
  if (/captain\s*\+\s*成员/.test(around)) return true;
  return false;
}

export function isExemptHit(
  text: string,
  term: string,
  index: number,
): boolean {
  if (isDefinitionalMention(text, term, index)) return true;
  if (term === "成员" && isExemptMemberUsage(text, index)) return true;
  // 「思考强度档」含「思考档」子串——勿误伤（黑名单未列「思考档」单独项，但防未来）
  if (term === "思考档" || term === "思考档位") {
    if (text.slice(index - 2, index + term.length).includes("思考强度档")) {
      return true;
    }
  }
  return false;
}

export interface TermHit {
  term: string;
  reason: string;
  snippet: string;
}

/** 扫描一段用户可见文案，返回未豁免的退役词命中。 */
export function findRetiredTermHits(text: string): TermHit[] {
  const hits: TermHit[] = [];
  for (const { term, reason } of RETIRED_TERMS) {
    let from = 0;
    while (from < text.length) {
      const index = text.indexOf(term, from);
      if (index < 0) break;
      if (!isExemptHit(text, term, index)) {
        hits.push({
          term,
          reason,
          snippet: windowAround(text, index, 20).replace(/\s+/g, " "),
        });
      }
      from = index + term.length;
    }
  }
  return hits;
}
