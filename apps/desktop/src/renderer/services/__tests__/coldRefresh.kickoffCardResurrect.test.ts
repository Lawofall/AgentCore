/**
 * Ctrl+R after 开做：开工卡复活 + 协作图消失（确定性复现）。
 *
 * 生产冷开序（ConversationPage）：硬刷新清 notedSettled / IX / execution →
 * adoptMessageWindow(磁盘 cache，不跑 toMessage) → GET messages 的 toMessage
 * 水合 IX（窗口被 isMessageWindowStrictlyRicher 拒写也不回滚）→ ResumePrompt
 * 走 selectVisibleColdResumes（leftover team_preview 不画可点开工壳）→ 图走 teamGraphVisible
 * （只看 IX 原始 status + execution.runs，不复用 settled 判据）。
 *
 * 事件字段抄自 conformance `gates._team_preview_finalized` / `team_preview_resolved`
 * （checkpoint_id=tp1, execution_id=exec1, tools/workers 原文）。
 */
import { teamGraphVisible } from "@/components/chat/debatePreviewPlacement";
import {
  type Message,
  getRuntime,
  isMessageWindowStrictlyRicher,
  useConversationStore,
} from "@/stores/conversation";
import {
  execRuntime,
  projectRuntime,
  useExecutionStore,
} from "@/stores/execution";
import {
  isColdCheckpointSettled,
  settledColdIdsFromEvents,
  useInteractionStore,
} from "@/stores/interactions";
import {
  clearColdServerSettled,
  isNotedColdServerSettled,
  journalSettledIdsFor,
} from "@/stores/interactions/coldSettlement";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import type { SSEEvent } from "@/types/events";
import { beforeEach, describe, expect, it } from "vitest";
import {
  type BackendMessage,
  shouldSetGeneratingOnHydrate,
  toMessage,
} from "../messages";
import { selectVisibleColdResumes } from "../resume";

const CID = "conv-kickoff-refresh";
const MID = "m1";
const TP = "tp1";

/** gates._team_preview_finalized workers / tools / run_plan — 逐字。 */
const WORKERS = [
  { run_id: "r1", role: "调研", task: "调研方案", depends_on: [] },
  { run_id: "r2", role: "撰写", task: "写初稿", depends_on: ["r1"] },
  { run_id: "r3", role: "核验", task: "对账", depends_on: ["r2"] },
  { run_id: "r4", role: "编辑", task: "修订", depends_on: ["r3"] },
  { run_id: "r5", role: "统稿", task: "成文", depends_on: ["r4"] },
];
const TOOLS = ["code_execute", "file_write", "test_run"];
const AGENTS = [
  { id: "w1", role: "调研", thinking: true },
  { id: "w2", role: "撰写", thinking: true },
  { id: "w3", role: "核验", thinking: true },
  { id: "w4", role: "编辑", thinking: true },
  { id: "w5", role: "统稿", thinking: true },
];
const PLAN_RUNS = [
  { id: "r1", agent_id: "w1", task: "调研方案", depends_on: [] },
  { id: "r2", agent_id: "w2", task: "写初稿", depends_on: ["r1"] },
  { id: "r3", agent_id: "w3", task: "对账", depends_on: ["r2"] },
  { id: "r4", agent_id: "w4", task: "修订", depends_on: ["r3"] },
  { id: "r5", agent_id: "w5", task: "成文", depends_on: ["r4"] },
];

const RUN_PLAN = {
  type: "run_plan",
  timestamp: "2026-01-01T00:00:00.000Z",
  payload: {
    execution_id: "exec1",
    plan_type: "multi_agent",
    task_summary: "构建 X",
    agents: AGENTS,
    runs: PLAN_RUNS,
  },
} as SSEEvent;

const TEAM_PREVIEW_REQUIRED = {
  type: "team_preview_required" as string,
  timestamp: "2026-01-01T00:00:01.000Z",
  payload: {
    checkpoint_id: TP,
    conversation_id: CID,
    workers: WORKERS,
    tools: TOOLS,
    primitive: "delegate",
    motion: "",
    form: "",
    sides: [],
    max_rounds: 0,
    thorough: true,
    revision: 1,
    headline: "预计 5 人开工",
  },
} as SSEEvent;

const TEAM_PREVIEW_RESOLVED = {
  type: "team_preview_resolved" as string,
  timestamp: "2026-01-01T00:00:02.000Z",
  payload: {
    checkpoint_id: TP,
    decision: "continue",
    note: "",
  },
} as SSEEvent;

function pauseSnapshotEvents(): SSEEvent[] {
  return [RUN_PLAN, TEAM_PREVIEW_REQUIRED];
}

function postKickoffEvents(): SSEEvent[] {
  return [RUN_PLAN, TEAM_PREVIEW_REQUIRED, TEAM_PREVIEW_RESOLVED];
}

function backendRow(
  events: SSEEvent[],
  extra: Partial<BackendMessage> = {},
): BackendMessage {
  return {
    id: MID,
    conversation_id: CID,
    role: "assistant",
    content: "我来安排团队。",
    reasoning_content: null,
    created_at: "2026-01-01T00:00:02.000Z",
    status: "running",
    paused: true,
    runs: {
      events,
      finish_reason: "paused",
    },
    ...extra,
  };
}

function seedUser(): void {
  useConversationStore.getState().switchConversation(CID);
  useConversationStore.getState().addMessage(
    {
      id: "u1",
      role: "user",
      content: "组 5 人做这份调研",
      createdAt: "2026-01-01T00:00:00.000Z",
      executionId: null,
      isStreaming: false,
    },
    CID,
  );
}

/** adoptMessageWindow：把已是 domain Message 的 cache 写进窗，不跑 toMessage。 */
function adoptCache(messages: Message[]): void {
  useConversationStore
    .getState()
    .setMessageWindow(
      messages,
      { hasMoreBefore: false, hasMoreAfter: false },
      CID,
    );
}

function paint() {
  const messages = getRuntime(CID).messages;
  const cards = selectVisibleColdResumes({
    conversationId: CID,
    byId: useInteractionStore.getState().byId,
    pausedPending: usePausedTurnStore.getState().pending,
    messages,
  });
  const entry = useInteractionStore.getState().byId.get(TP);
  const journalIds = settledColdIdsFromEvents(
    messages.flatMap((m) => m.runs?.events ?? []),
  );
  const previews = useInteractionStore
    .getState()
    .listPending(CID, ["team_preview"]);
  const assistant = messages.find((m) => m.id === MID);
  if (assistant?.runs) {
    useExecutionStore.getState().hydrateFromJournal(MID, assistant.runs);
  }
  const projected = projectRuntime(
    execRuntime(useExecutionStore.getState(), MID),
  );
  const runs = projected?.runs ?? [];
  const graph = teamGraphVisible(runs);
  const settled = isColdCheckpointSettled({
    checkpointId: TP,
    entry,
    journalSettledIds: journalIds,
  });
  return {
    cards: cards.map((c) => ({
      kind: c.kind,
      checkpointId: c.checkpointId,
      headline: c.headline,
    })),
    ixStatus: entry?.status ?? null,
    ixDecision: entry?.resolution?.decision ?? null,
    journalHasResolved: journalIds.has(TP),
    noted: isNotedColdServerSettled(TP),
    readerHasResolved: journalSettledIdsFor(CID).has(TP),
    settled,
    previewStatuses: previews.map((p) => ({
      kind: p.kind,
      status: p.status,
    })),
    runStatuses: runs.map((r) => ({ status: r.status, kind: r.kind ?? null })),
    graph,
    streaming: assistant?.isStreaming ?? null,
    generatingChrome: shouldSetGeneratingOnHydrate(messages),
    sendNotStop:
      shouldSetGeneratingOnHydrate(messages) === false && cards.length > 0,
  };
}

/** The seeded user row every cache/GET window must carry back in. */
function seededUserMessage() {
  const user = getRuntime(CID).messages.find((m) => m.role === "user");
  if (!user) throw new Error("seedUser() must run before building a window");
  return user;
}

beforeEach(() => {
  clearColdServerSettled();
  usePausedTurnStore.getState().clear();
  useInteractionStore.getState().clear();
  useExecutionStore.setState({ byId: {} });
  useConversationStore.setState({ currentConversationId: null, byId: {} });
});

describe("Ctrl+R after 开做 — leftover 不画可点开工壳 + 图消失", () => {
  it("坏序：cache 开做前快照 + GET 仍是 paused 投影（无 *_resolved）→ leftover 不画可点开工壳、图随 run_plan 出", () => {
    seedUser();
    // 磁盘 cache 来自上次 GET（开工前挂起窗）。adopt 不跑 toMessage，IX 空。
    const cached = toMessage(backendRow(pauseSnapshotEvents()));
    // 上面 toMessage 是为了做出「已是 domain Message」的 cache 形状；刷新后 store 是空的。
    useInteractionStore.getState().clear();
    usePausedTurnStore.getState().clear();
    clearColdServerSettled();
    useExecutionStore.setState({ byId: {} });
    adoptCache([
      seededUserMessage(),
      {
        ...cached,
        // cache 行不带 IX 副作用；process 比 GET 厚 → 拒写仍能发生
        process: [
          ...(cached.process ?? []),
          { kind: "reasoning", text: "cache-only marker" },
        ],
      },
    ]);
    expect(useInteractionStore.getState().byId.size).toBe(0);

    const getMsg = toMessage(backendRow(pauseSnapshotEvents()));
    const existing = getRuntime(CID).messages;
    const incoming = [seededUserMessage(), getMsg];
    const wrote = isMessageWindowStrictlyRicher(incoming, existing);
    // cache 更厚 → GET 拒写；leftover 开工卡事件不再水合 IX
    expect(wrote).toBe(false);
    expect(useInteractionStore.getState().byId.get(TP)).toBeUndefined();

    const snap = paint();
    // eslint-disable-next-line no-console -- 验收要贴真实快照，不是「已通过」
    console.log("BAD_HYDRATE_SNAPSHOT", JSON.stringify(snap, null, 2));

    expect(snap.settled).toBe(false);
    expect(snap.journalHasResolved).toBe(false);
    expect(snap.noted).toBe(false);
    expect(snap.ixStatus).toBeNull();
    expect(snap.cards).toEqual([]);
    expect(snap.graph).toBe(true);
    expect(snap.streaming).toBe(false);
    expect(snap.generatingChrome).toBe(false);
    expect(snap.sendNotStop).toBe(false);
  });

  it("好序：同一 refresh，GET journal 带 team_preview_resolved → 卡不画、编制出图", () => {
    seedUser();
    const getMsg = toMessage(
      backendRow(postKickoffEvents(), { paused: false, status: "running" }),
    );
    adoptCache([seededUserMessage(), getMsg]);

    const snap = paint();
    // eslint-disable-next-line no-console
    console.log("GOOD_HYDRATE_SNAPSHOT", JSON.stringify(snap, null, 2));

    expect(snap.settled).toBe(true);
    expect(snap.journalHasResolved).toBe(true);
    expect(snap.ixStatus).toBeNull();
    expect(snap.ixDecision).toBeNull();
    expect(snap.cards).toEqual([]);
    expect(snap.graph).toBe(true);
    expect(snap.sendNotStop).toBe(false);
  });

  it("GET 有 resolved 但窗口拒写：卡仍被 IX.status 挡住（不是本 bug）", () => {
    seedUser();
    const cached = toMessage(backendRow(pauseSnapshotEvents()));
    useInteractionStore.getState().clear();
    usePausedTurnStore.getState().clear();
    clearColdServerSettled();
    adoptCache([
      seededUserMessage(),
      {
        ...cached,
        process: [
          ...(cached.process ?? []),
          { kind: "reasoning", text: "thicker-cache" },
        ],
      },
    ]);

    toMessage(backendRow(postKickoffEvents(), { paused: false }));
    expect(
      isMessageWindowStrictlyRicher(
        [
          seededUserMessage(),
          toMessage(backendRow(postKickoffEvents(), { paused: false })),
        ],
        getRuntime(CID).messages,
      ),
    ).toBe(false);
    // leftover 事件对不再 upsert IX
    expect(useInteractionStore.getState().byId.get(TP)).toBeUndefined();

    const snap = paint();
    // eslint-disable-next-line no-console
    console.log("REJECT_WINDOW_BUT_IX_RESOLVED", JSON.stringify(snap, null, 2));

    expect(snap.journalHasResolved).toBe(false);
    expect(snap.ixStatus).toBeNull();
    expect(snap.settled).toBe(false);
    expect(snap.cards).toEqual([]);
  });

  it("isColdCheckpointSettled 在坏序三腿皆空：这就是卡闸没挡住的原因", () => {
    seedUser();
    toMessage(backendRow(pauseSnapshotEvents()));
    adoptCache([
      seededUserMessage(),
      toMessage(backendRow(pauseSnapshotEvents())),
    ]);

    const entry = useInteractionStore.getState().byId.get(TP);
    const journalIds = settledColdIdsFromEvents(
      getRuntime(CID).messages.flatMap((m) => m.runs?.events ?? []),
    );
    expect(isNotedColdServerSettled(TP)).toBe(false);
    expect(entry).toBeUndefined();
    expect(journalIds.has(TP)).toBe(false);
    expect(
      isColdCheckpointSettled({
        checkpointId: TP,
        entry,
        journalSettledIds: journalIds,
      }),
    ).toBe(false);
  });
});
