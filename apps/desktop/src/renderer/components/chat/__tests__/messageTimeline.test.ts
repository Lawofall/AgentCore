import type { BrowserTakeover } from "@/stores/browserTakeover";
import type { MemoryUpdate, Message } from "@/stores/conversation";
import type { PermissionChange } from "@/stores/permissionChanges";
import { describe, expect, it } from "vitest";
import { mergeTimeline } from "../messageTimeline";

/**
 * mergeTimeline — 记忆卡不再按裸时间戳就地插，而是锚到「它所在那一回合的末尾」（AI 回答之后、
 * 下一次提问之前），既不夹进「提问↔回答」对里，又每回合各一张、按时间分布，不堆在对话底部
 * （记忆更新对话内可见 §1.6）。
 */

const um = (id: string, at: string): Message =>
  ({
    id,
    role: "user",
    content: "",
    createdAt: at,
    executionId: null,
    isStreaming: false,
  }) as Message;

const am = (id: string, at: string): Message =>
  ({
    id,
    role: "assistant",
    content: "",
    createdAt: at,
    executionId: null,
    isStreaming: false,
  }) as Message;

// `at` = 落库时刻 (created_at); `anchorAt` = 被总结那一轮的末尾, 有则定位用它。
const mem = (
  id: string,
  at: string,
  anchorAt: string | null = null,
): MemoryUpdate => ({
  id,
  createdAt: at,
  anchorAt,
  kind: "semantic",
  items: [],
});

// A takeover marker anchors on its START time (endedAt is irrelevant to placement).
const tko = (id: string, startedAt: string): BrowserTakeover => ({
  id,
  startedAt,
  endedAt: startedAt,
});

// A preset switch anchors on when it happened (下一回合生效 → lands before the next turn).
const pc = (id: string, at: string): PermissionChange => ({
  id,
  at,
  previous: "observe",
  next: "full_trust",
});

describe("mergeTimeline", () => {
  it("returns a pure message list when there are no tasks or memory cards", () => {
    const items = mergeTimeline(
      [um("u1", "2026-01-01T00:00:00Z"), am("a1", "2026-01-01T01:00:00Z")],
      [],
    );
    expect(items.map((i) => i.kind)).toEqual(["message", "message"]);
    expect(items.map((i) => i.key)).toEqual(["m:u1", "m:a1"]);
  });

  it("pushes a mid-turn card to the exchange end, never between question and answer", () => {
    // The long turn's answer (a1) is stored with its turn-COMPLETION time (02:00); the
    // offline card consolidated at 01:00 — a raw time-sort would slip it between u1 and a1.
    // It must instead land AFTER the answer (end of the exchange).
    const messages = [
      um("u1", "2026-01-01T00:00:00Z"),
      am("a1", "2026-01-01T02:00:00Z"),
    ];
    const memory = [mem("mem1", "2026-01-01T01:00:00Z")];
    const items = mergeTimeline(messages, [], memory);
    expect(items.map((i) => i.key)).toEqual(["m:u1", "m:a1", "mem:mem1"]);
  });

  it("anchors each turn's card to its own exchange end, distributed not stacked", () => {
    const messages = [
      um("u1", "2026-01-01T00:00:00Z"),
      am("a1", "2026-01-01T01:00:00Z"),
      um("u2", "2026-01-01T02:00:00Z"),
      am("a2", "2026-01-01T05:00:00Z"),
    ];
    // Unsorted input; mem1 belongs to turn 1 (before u2), mem2 to turn 2 (tail).
    const memory = [
      mem("mem2", "2026-01-01T06:00:00Z"),
      mem("mem1", "2026-01-01T01:30:00Z"),
    ];
    const items = mergeTimeline(messages, [], memory);
    expect(items.map((i) => i.key)).toEqual([
      "m:u1",
      "m:a1",
      "mem:mem1",
      "m:u2",
      "m:a2",
      "mem:mem2",
    ]);
  });

  it("keeps a card whose timestamp lands inside a later long turn out of that turn's Q→A", () => {
    // turn-1 consolidation lagged into turn-2's window (03:00, between u2 and its 05:00
    // answer): it snaps to the tail of turn 2, not between u2 and a2.
    const messages = [
      um("u1", "2026-01-01T00:00:00Z"),
      am("a1", "2026-01-01T01:00:00Z"),
      um("u2", "2026-01-01T02:00:00Z"),
      am("a2", "2026-01-01T05:00:00Z"),
    ];
    const memory = [mem("memX", "2026-01-01T03:00:00Z")];
    const items = mergeTimeline(messages, [], memory);
    expect(items.map((i) => i.key)).toEqual([
      "m:u1",
      "m:a1",
      "m:u2",
      "m:a2",
      "mem:memX",
    ]);
  });

  it("anchors on anchor_at when consolidation landed after the next question", () => {
    // 真实案例：固化是回合结束后异步跑的，卡片落库(10:03:01)比下一条提问(10:03:00)还晚 1 秒。
    // 它总结的是 u1→a1 这一轮，anchor_at = 该轮最后一条消息(a1, 10:01:30)，卡片就该留在那一轮
    // 末尾；只看落库时刻则永远锚不上任何一条后续提问，卡片会冲到列表最末。
    const messages = [
      um("u1", "2026-01-01T10:00:00Z"),
      am("a1", "2026-01-01T10:01:30Z"),
      um("u2", "2026-01-01T10:03:00Z"),
    ];
    const items = mergeTimeline(
      messages,
      [],
      [mem("mem1", "2026-01-01T10:03:01Z", "2026-01-01T10:01:30Z")],
    );
    expect(items.map((i) => i.key)).toEqual([
      "m:u1",
      "m:a1",
      "mem:mem1",
      "m:u2",
    ]);

    // 同一张卡没有 anchor_at 时（老数据）仍按落库时刻走。
    const fallback = mergeTimeline(
      messages,
      [],
      [mem("mem1", "2026-01-01T10:03:01Z")],
    );
    expect(fallback.map((i) => i.key)).toEqual([
      "m:u1",
      "m:a1",
      "m:u2",
      "mem:mem1",
    ]);
  });

  it("places a card tied with a user message's timestamp at that exchange's end", () => {
    const messages = [
      um("u1", "2026-01-01T00:00:00Z"),
      am("a1", "2026-01-01T01:00:00Z"),
      um("u2", "2026-01-01T02:00:00Z"),
      am("a2", "2026-01-01T03:00:00Z"),
    ];
    const memory = [mem("memT", "2026-01-01T02:00:00Z")];
    const items = mergeTimeline(messages, [], memory);
    expect(items.map((i) => i.key)).toEqual([
      "m:u1",
      "m:a1",
      "m:u2",
      "m:a2",
      "mem:memT",
    ]);
  });

  it("anchors a takeover marker to its exchange end (like a memory card)", () => {
    // A takeover happened at 01:00, between u1 and the long turn's 02:00 completion;
    // it must land AFTER the answer (exchange tail), never between question and answer.
    const messages = [
      um("u1", "2026-01-01T00:00:00Z"),
      am("a1", "2026-01-01T02:00:00Z"),
    ];
    const items = mergeTimeline(
      messages,
      [],
      [],
      [tko("t1", "2026-01-01T01:00:00Z")],
    );
    expect(items.map((i) => i.key)).toEqual(["m:u1", "m:a1", "tko:t1"]);
  });

  it("distributes takeover markers across their own exchanges, not stacked at the tail", () => {
    const messages = [
      um("u1", "2026-01-01T00:00:00Z"),
      am("a1", "2026-01-01T01:00:00Z"),
      um("u2", "2026-01-01T02:00:00Z"),
      am("a2", "2026-01-01T05:00:00Z"),
    ];
    const takeovers = [
      tko("t2", "2026-01-01T06:00:00Z"), // turn 2 tail
      tko("t1", "2026-01-01T01:30:00Z"), // turn 1 (before u2)
    ];
    const items = mergeTimeline(messages, [], [], takeovers);
    expect(items.map((i) => i.key)).toEqual([
      "m:u1",
      "m:a1",
      "tko:t1",
      "m:u2",
      "m:a2",
      "tko:t2",
    ]);
  });

  it("anchors a permission-change line before the next turn it governs", () => {
    // Switch happened at 01:30 (after turn 1's answer, before u2); the「权限模式 A → B」line
    // must land at turn 1's tail — right ahead of the turn 2 it takes effect on.
    const messages = [
      um("u1", "2026-01-01T00:00:00Z"),
      am("a1", "2026-01-01T01:00:00Z"),
      um("u2", "2026-01-01T02:00:00Z"),
      am("a2", "2026-01-01T03:00:00Z"),
    ];
    const items = mergeTimeline(
      messages,
      [],
      [],
      [],
      [pc("pc1", "2026-01-01T01:30:00Z")],
    );
    expect(items.map((i) => i.key)).toEqual([
      "m:u1",
      "m:a1",
      "pc:pc1",
      "m:u2",
      "m:a2",
    ]);
  });

  it("interleaves memory + takeover cards, ordered by time within an exchange", () => {
    const messages = [
      um("u1", "2026-01-01T00:00:00Z"),
      am("a1", "2026-01-01T01:00:00Z"),
      um("u2", "2026-01-01T10:00:00Z"),
    ];
    // Both belong to turn 1 (before u2). Takeover (02:00) precedes memory (03:00).
    const items = mergeTimeline(
      messages,
      [],
      [mem("m1", "2026-01-01T03:00:00Z")],
      [tko("t1", "2026-01-01T02:00:00Z")],
    );
    expect(items.map((i) => i.key)).toEqual([
      "m:u1",
      "m:a1",
      "tko:t1",
      "mem:m1",
      "m:u2",
    ]);
  });

  it("inserts a compaction divider after the last folded message", () => {
    const messages = [
      um("u1", "2026-01-01T00:00:00Z"),
      am("a1", "2026-01-01T01:00:00Z"),
      um("u2", "2026-01-01T02:00:00Z"),
      am("a2", "2026-01-01T03:00:00Z"),
    ];
    const items = mergeTimeline(
      messages,
      [],
      [],
      [],
      [],
      "2026-01-01T01:00:00Z",
    );
    expect(items.map((i) => i.key)).toEqual([
      "m:u1",
      "m:a1",
      "compaction",
      "m:u2",
      "m:a2",
    ]);
  });

  it("does not show a compaction divider when the fold is above the loaded window", () => {
    const messages = [
      um("u2", "2026-01-01T02:00:00Z"),
      am("a2", "2026-01-01T03:00:00Z"),
    ];
    const items = mergeTimeline(
      messages,
      [],
      [],
      [],
      [],
      "2026-01-01T01:00:00Z",
    );
    expect(items.map((i) => i.key)).toEqual(["m:u2", "m:a2"]);
  });

  it("keeps the compaction divider off the composer when the chat is not folded", () => {
    const items = mergeTimeline(
      [um("u1", "2026-01-01T00:00:00Z"), am("a1", "2026-01-01T01:00:00Z")],
      [],
    );
    expect(items.some((i) => i.kind === "compaction")).toBe(false);
  });
});
