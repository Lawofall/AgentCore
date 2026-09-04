/**
 * 终审简报展示拆句：契约字段仍是自由文，UI 拆成站队 / 命题 / 反转 / 问句 / 对照。
 * 不改 wire；旧场长句与新场短句都能读。
 */

export type StanceSide = "pro" | "con" | null;

export type LeaningDisplay = {
  stanceLabel: string | null;
  stanceSide: StanceSide;
  thesis: string;
  reversal: string | null;
};

export type ValueCallDisplay = {
  question: string;
  mappings: string[];
};

export type FactDisplay = {
  body: string;
  statusLabels: string[];
};

const STANCE_PREFIX = /^(倾向[^\s：:。；，,]{1,12})(?:\s*[：:。]\s*|\s+|$)/u;

const REVERSAL_TAIL = /[；。]\s*(若[\s\S]*则翻[\s\S]*)$/u;

export function splitLeaning(
  raw: string,
  sideHints?: ReadonlyArray<{ name: string; key: string; stance?: string }>,
): LeaningDisplay {
  const text = raw.trim();
  if (!text) {
    return { stanceLabel: null, stanceSide: null, thesis: "", reversal: null };
  }

  let stanceLabel: string | null = null;
  let rest = text;
  const prefix = STANCE_PREFIX.exec(text);
  if (prefix) {
    stanceLabel = prefix[1] ?? null;
    rest = text.slice(prefix[0].length).trim();
  }

  let reversal: string | null = null;
  const tail = REVERSAL_TAIL.exec(rest);
  if (tail) {
    rest = rest.slice(0, tail.index).trim();
    reversal = (tail[1] ?? "").trim() || null;
  } else if (/^若/.test(rest) && /则翻/.test(rest)) {
    reversal = rest;
    rest = "";
  }

  return {
    stanceLabel,
    stanceSide: stanceSideFromLabel(stanceLabel, sideHints),
    thesis: rest,
    reversal,
  };
}

export function splitValueCall(raw: string): ValueCallDisplay {
  const text = raw.trim();
  if (!text) return { question: "", mappings: [] };

  const mappingRe = /选\s*([^→\-]{1,40}?)\s*(?:→|->)\s*([^；。]+)/gu;
  const mappings: string[] = [];
  let firstMap = -1;
  let match = mappingRe.exec(text);
  while (match) {
    if (firstMap < 0) firstMap = match.index;
    const left = (match[1] ?? "").trim();
    const right = (match[2] ?? "").trim();
    if (left && right) mappings.push(`${left} → ${right}`);
    match = mappingRe.exec(text);
  }

  let question = firstMap >= 0 ? text.slice(0, firstMap).trim() : text;
  question = question.replace(/[；。]+$/u, "").trim();
  return { question: question || text, mappings };
}

export function splitFactDisplay(raw: string): FactDisplay {
  const statusLabels: string[] = [];
  const statusRe = /【([^】]{1,24})】/gu;
  let mark = statusRe.exec(raw);
  while (mark) {
    const label = (mark[1] ?? "").trim();
    if (label && !statusLabels.includes(label)) statusLabels.push(label);
    mark = statusRe.exec(raw);
  }

  const body = raw
    .replace(/【([^】]{1,24})】/gu, " ")
    .replace(/[（(][^）)]*(?:#e\d+|tier\s*=|unknown待评)[^）)]*[）)]/gi, " ")
    .replace(/(?:tier\s*=\s*\w+|unknown待评|#e\d+)/gi, " ")
    .replace(/\(\s*[,，;；/]*\s*\)/g, " ")
    .replace(/（\s*[,，;；/]*\s*）/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();

  return { body, statusLabels };
}

function stanceSideFromLabel(
  label: string | null,
  sideHints?: ReadonlyArray<{ name: string; key: string; stance?: string }>,
): StanceSide {
  if (!label) return null;
  if (label.includes("反方")) return "con";
  if (label.includes("正方")) return "pro";
  const hit = sideHints?.find((s) => s.name && label.includes(s.name));
  if (!hit) return null;
  if (hit.stance === "con" || hit.key === "con") return "con";
  if (hit.stance === "pro" || hit.key === "pro") return "pro";
  return null;
}
