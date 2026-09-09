/**
 * 场级证据台账（evidence_ledger）前端工具——解析成稿 `#eN`、合并 live delta、徽章文案。
 * 与后端 `runtime/debate/evidence_ledger.py` / 提案 O1·O4·O5 同源约定。
 */

import type { EvidenceLedgerEntry } from "@/types/events";

/** 成稿 note 中的台账 id（`#e3`；双写「出处短语 #e3」亦可抽出）。 */
const LEDGER_ID_RE = /#e\d+/;

/** 从标记 note 抽出第一个 `#eN`；无则 null（旧自由文本出处）。 */
export function extractLedgerId(note: string): string | null {
  const m = LEDGER_ID_RE.exec(note);
  return m ? m[0] : null;
}

/** 按 id 合并增量（后写覆盖）；保持登记序（先出现的 id 在前）。 */
export function mergeEvidenceLedger(
  existing: readonly EvidenceLedgerEntry[],
  delta: readonly EvidenceLedgerEntry[],
): EvidenceLedgerEntry[] {
  if (delta.length === 0) return [...existing];
  const order: string[] = [];
  const byId = new Map<string, EvidenceLedgerEntry>();
  for (const e of existing) {
    if (!byId.has(e.id)) order.push(e.id);
    byId.set(e.id, e);
  }
  for (const e of delta) {
    if (!byId.has(e.id)) order.push(e.id);
    byId.set(e.id, e);
  }
  return order
    .map((id) => byId.get(id))
    .filter((e): e is EvidenceLedgerEntry => e !== undefined);
}

/** id → 条目；供徽章 O(1) 查表。 */
export function buildLedgerMap(
  entries: readonly EvidenceLedgerEntry[],
): ReadonlyMap<string, EvidenceLedgerEntry> {
  const m = new Map<string, EvidenceLedgerEntry>();
  for (const e of entries) m.set(e.id, e);
  return m;
}

/** 徽章可见出处：优先 site，其次 title，最后回退 id。 */
export function ledgerBadgeLabel(entry: EvidenceLedgerEntry): string {
  const site = (entry.site ?? "").trim();
  if (site) return site;
  const title = (entry.title ?? "").trim();
  if (title) return title;
  return entry.id;
}

/** 日期展示：空 →「日期未知」（不是质量档）。 */
export function ledgerDateLabel(date: string | undefined): string {
  const d = (date ?? "").trim();
  return d || "日期未知";
}
