/**
 * Settled ask scan line.
 *
 * The header is the same row collapsed or open: a short stem (first sentence,
 * parenthetical hints stay in the full text) and the reply. A short reply stays
 * at the end of the row. Several short replies join with 「 · 」; past the scan
 * budget they fold to 「共 N 题」.
 *
 * The body only carries what that row cannot: the full prompt, and replies when
 * they were folded or when more than one question needs its own pair. The CEO
 * compose string（题干：答复）stays off this face.
 */

import { collapsedAskGlance, displayAskReply } from "./AskUserFields";

/** Joined replies longer than this leave the scan line as 「共 N 题」. */
const HEADER_ANSWER_MAX = 32;

const COMPOSE_COLON = "：";
const SUPPLEMENT = " · 补充：";

export type SettledAskItem = {
  prompt: string;
  picks: readonly string[];
  /** Free text or 补充. Empty when picks are the whole reply. */
  reply: string;
};

export type SettledAskFace = {
  /** Scan stem. Empty when the row has no question. */
  stem: string;
  /**
   * Full question the stem was cut from, when that text is not already one of
   * `items` and is not a batch lede (`prompts` present). Empty otherwise.
   */
  lead: string;
  /** Scan-line conclusion. May be 「共 N 题」. */
  answer: string;
  /** False when `answer` dropped reply text the body must show. */
  answersInHeader: boolean;
  items: SettledAskItem[];
};

export type SettledAskBlock = {
  prompt: string;
  picks: readonly string[];
  reply: string;
};

export function itemAnswer(item: SettledAskItem): string {
  return [item.picks.join(" · "), item.reply].filter(Boolean).join(" · ");
}

/** First sentence, with （…） / (…) hints removed. */
export function scanStem(prompt: string): string {
  const original = prompt.trim();
  if (!original) return "";
  const stripped = original
    .replace(/（[^）]*）/g, "")
    .replace(/\([^)]*\)/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const base = stripped || original;
  const cut = base.search(/[？?。！!]/);
  if (cut === -1) return base;
  const sentence = base.slice(0, cut + 1).trim();
  return sentence || base;
}

function splitSupplement(value: string): { main: string; supplement: string } {
  const at = value.indexOf(SUPPLEMENT);
  if (at === -1) return { main: value.trim(), supplement: "" };
  return {
    main: value.slice(0, at).trim(),
    supplement: value.slice(at + SUPPLEMENT.length).trim(),
  };
}

function supplementIn(note: string): string {
  const cleaned = displayAskReply(note.trim());
  const marker = "补充：";
  const at = cleaned.indexOf(marker);
  if (at === -1) return "";
  return (
    cleaned
      .slice(at + marker.length)
      .split("\n")[0]
      ?.trim() ?? ""
  );
}

function replyForPrompt(note: string, prompt: string): string {
  const prefix = `${prompt}${COMPOSE_COLON}`;
  for (const raw of displayAskReply(note.trim()).split("\n")) {
    const line = raw.trim();
    if (line.startsWith(prefix)) return line.slice(prefix.length).trim();
  }
  return "";
}

function legacyPairs(note: string): { prompt: string; reply: string }[] {
  const cleaned = displayAskReply(note.trim());
  if (!cleaned) return [];
  const out: { prompt: string; reply: string }[] = [];
  for (const raw of cleaned.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const at = line.indexOf(COMPOSE_COLON);
    if (at === -1) {
      out.push({ prompt: "", reply: line });
      continue;
    }
    const lhs = line.slice(0, at).trim();
    const rhs = line.slice(at + COMPOSE_COLON.length).trim();
    if (!rhs) continue;
    const { main, supplement } = splitSupplement(rhs);
    out.push({
      prompt: lhs,
      reply: [main, supplement].filter(Boolean).join(" · "),
    });
  }
  return out;
}

function itemForPrompt(
  prompt: string,
  note: string,
  selected: readonly string[],
  sole: boolean,
): SettledAskItem {
  const parsed = replyForPrompt(note, prompt);
  const { main, supplement } = splitSupplement(parsed);
  if (sole && selected.length > 0) {
    return { prompt, picks: selected, reply: supplement || supplementIn(note) };
  }
  if (sole && !main && !supplement) {
    return {
      prompt,
      picks: [],
      reply: collapsedAskGlance({ selected: [], note, prompts: [prompt] }),
    };
  }
  return {
    prompt,
    picks: [],
    reply: [main, supplement].filter(Boolean).join(" · "),
  };
}

function buildItems(
  question: string,
  prompts: readonly string[],
  note: string,
  selected: readonly string[],
): SettledAskItem[] {
  if (prompts.length > 0) {
    const items = prompts.map((prompt) =>
      itemForPrompt(prompt, note, selected, prompts.length === 1),
    );
    if (
      items.every((item) => itemAnswer(item) === "") &&
      selected.length > 0 &&
      items[0]
    ) {
      items[0] = { ...items[0], picks: selected };
    }
    return items.filter(
      (item) => item.prompt || item.picks.length > 0 || item.reply,
    );
  }

  if (selected.length > 0) {
    return [
      {
        prompt: question.trim(),
        picks: selected,
        reply: supplementIn(note),
      },
    ];
  }

  const pairs = legacyPairs(note);
  if (pairs.length >= 2) {
    return pairs.map((pair) => ({
      prompt: pair.prompt,
      picks: [],
      reply: pair.reply,
    }));
  }
  if (pairs.length === 1) {
    const asked =
      pairs[0].prompt && question.trim() && pairs[0].prompt !== question.trim()
        ? pairs[0].prompt
        : question.trim() || pairs[0].prompt;
    return [
      {
        prompt: asked,
        picks: [],
        reply: pairs[0].reply,
      },
    ];
  }
  const free = collapsedAskGlance({ selected: [], note, prompts: [] });
  if (!question.trim() && !free) return [];
  return [{ prompt: question.trim(), picks: [], reply: free }];
}

export function settledAskFace(input: {
  question: string;
  prompts: readonly string[];
  note: string;
  selected: readonly string[];
}): SettledAskFace {
  const prompts = input.prompts.map((p) => p.trim()).filter(Boolean);
  const selected = input.selected.map((s) => s.trim()).filter(Boolean);
  const items = buildItems(input.question, prompts, input.note, selected);
  const question = input.question.trim();
  const stemSource =
    prompts[0] ||
    (items.length >= 2 ? question : "") ||
    items[0]?.prompt ||
    question;
  const stem = scanStem(stemSource);
  const lead =
    prompts.length === 0 &&
    question !== "" &&
    question !== stem &&
    !items.some((item) => item.prompt.trim() === question)
      ? question
      : "";
  const parts = items.map(itemAnswer).filter(Boolean);
  const joined = parts.join(" · ");
  if (!joined) {
    return { stem, lead, answer: "", answersInHeader: true, items };
  }
  if (parts.length >= 2 && joined.length > HEADER_ANSWER_MAX) {
    return {
      stem,
      lead,
      answer: `共 ${parts.length} 题`,
      answersInHeader: false,
      items,
    };
  }
  return {
    stem,
    lead,
    answer: joined,
    answersInHeader: joined.length <= HEADER_ANSWER_MAX,
    items,
  };
}

/** Blocks the open row adds under the scan line. Empty → the line is the record. */
export function settledAskBlocks(
  face: SettledAskFace,
  stemInHeader: boolean,
): SettledAskBlock[] {
  const multi = face.items.length > 1;
  const blocks: SettledAskBlock[] = [];
  if (stemInHeader && face.lead) {
    blocks.push({ prompt: face.lead, picks: [], reply: "" });
  }
  for (const item of face.items) {
    const promptInHeader = stemInHeader && item.prompt.trim() === face.stem;
    const showPrompt = item.prompt.trim() !== "" && (multi || !promptInHeader);
    const showReply = multi || !face.answersInHeader;
    const picks = showReply ? item.picks : [];
    const reply = showReply ? item.reply : "";
    if (!showPrompt && picks.length === 0 && !reply) continue;
    blocks.push({
      prompt: showPrompt ? item.prompt : "",
      picks,
      reply,
    });
  }
  if (!stemInHeader && blocks.length === 0 && face.stem) {
    blocks.push({ prompt: face.stem, picks: [], reply: "" });
  }
  return blocks;
}
