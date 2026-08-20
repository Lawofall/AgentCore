import {
  EXECUTION_HARVEST_ORIGIN,
  isExecutionHarvestMessage,
} from "@/lib/executionHarvest";
import { useConversationStore } from "@/stores/conversation";
import { useExecutionStore } from "@/stores/execution";
import { useInteractionStore } from "@/stores/interactions";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import { beforeEach, describe, expect, it } from "vitest";
import {
  type BackendMessage,
  shouldSetGeneratingOnHydrate,
  toMessage,
} from "../messages";

/** Minimal persisted row — enough for `toMessage` hydrate assertions. */
function row(
  over: Partial<BackendMessage> & Pick<BackendMessage, "id" | "role">,
): BackendMessage {
  return {
    conversation_id: "c1",
    content: "hello",
    reasoning_content: null,
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

beforeEach(() => {
  usePausedTurnStore.getState().clear();
  useInteractionStore.getState().clear();
  useExecutionStore.setState({ byId: {} });
  useConversationStore.setState({ currentConversationId: null, byId: {} });
});

describe("toMessage (reload hydrate)", () => {
  it("stamps serverMessageId = row id on assistant so resume guards match live", () => {
    const msg = toMessage(
      row({ id: "srv-msg-1", role: "assistant", content: "ok" }),
    );

    expect(msg.id).toBe("srv-msg-1");
    expect(msg.role).toBe("assistant");
    expect(msg.serverMessageId).toBe("srv-msg-1");
  });

  it("stamps execution_harvest origin so hydrate still hides the synthetic user", () => {
    const msg = toMessage(
      row({
        id: "u-harvest",
        role: "user",
        content: "hi",
        origin: EXECUTION_HARVEST_ORIGIN,
      }),
    );
    expect(msg.origin).toBe(EXECUTION_HARVEST_ORIGIN);
    expect(isExecutionHarvestMessage(msg)).toBe(true);
  });

  it("does not stamp serverMessageId on user rows", () => {
    const msg = toMessage(
      row({ id: "srv-user-1", role: "user", content: "hi" }),
    );

    expect(msg.id).toBe("srv-user-1");
    expect(msg.role).toBe("user");
    expect(msg.serverMessageId).toBeUndefined();
  });

  it("maps REST agent_mentions onto history user-bubble chips", () => {
    const msg = toMessage(
      row({
        id: "u-mention",
        role: "user",
        content: "帮我调研",
        agent_mentions: [{ agent_id: "w1", role: "研究员" }],
      }),
    );
    expect(msg.agentMentions).toEqual([{ agentId: "w1", role: "研究员" }]);
  });

  it("maps status=running (no paused) to isStreaming for overlay partial", () => {
    const msg = toMessage(
      row({
        id: "m-live",
        role: "assistant",
        content: "partial…",
        status: "running",
      }),
    );
    expect(msg.isStreaming).toBe(true);
    expect(msg.finishReason).toBeUndefined();
    expect(shouldSetGeneratingOnHydrate([msg])).toBe(true);
  });

  it("maps status=running + paused to non-streaming finishReason=paused", () => {
    // Write latch keeps status=running; read lifts paused so reopen is not「仍在生成」.
    const msg = toMessage(
      row({
        id: "m-paused",
        role: "assistant",
        content: "checkpoint body",
        status: "running",
        paused: true,
      }),
    );
    expect(msg.isStreaming).toBe(false);
    expect(msg.finishReason).toBe("paused");
    expect(msg.status).toBe("running");
    expect(shouldSetGeneratingOnHydrate([msg])).toBe(false);
  });

  it("carries the 曾中断恢复 marker so a redriven turn is not silently normal", () => {
    // 崩溃重驱恢复归属原回合 (D5)：成果落回原消息，标记必须跟着一起回放。
    const recovered = toMessage(
      row({
        id: "m-recovered",
        role: "assistant",
        content: "完整成果",
        status: "complete",
        recovered: true,
      }),
    );
    expect(recovered.recovered).toBe(true);

    const plain = toMessage(
      row({ id: "m-plain", role: "assistant", content: "一次跑完" }),
    );
    expect(plain.recovered).toBeUndefined();
  });

  it("reloads face from usage.error when runs.error is absent (REST path)", () => {
    const msg = toMessage(
      row({
        id: "m-usage-err",
        role: "assistant",
        content: "",
        status: "failed",
        usage: {
          input: 0,
          output: 0,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 0,
          error: {
            code: "LLM_TIMEOUT",
            message: "连接超时，请检查网络后重试。",
          },
        },
      }),
    );
    expect(msg.error).toEqual({
      code: "LLM_TIMEOUT",
      message: "连接超时，请检查网络后重试。",
    });
  });

  it("does not set generating chrome when last message is cold-paused", () => {
    const live = toMessage(
      row({ id: "m1", role: "user", content: "q", status: null }),
    );
    const paused = toMessage(
      row({
        id: "m2",
        role: "assistant",
        content: "a",
        status: "running",
        paused: true,
      }),
    );
    expect(shouldSetGeneratingOnHydrate([live, paused])).toBe(false);
  });

  it("classic reload: keeps runs + hydrates interjections without run_plan", () => {
    // 协作图文档五态：经典单聊刷新后插话仍在。REST 把 user_interjection 放在
    // runs.events，但无 run_plan → executionId null；旧路径丢弃 runs 导致 store 空。
    const msg = toMessage(
      row({
        id: "m-classic",
        role: "assistant",
        content: "总结如下…",
        status: "complete",
        runs: {
          events: [
            {
              type: "user_interjection",
              timestamp: "2026-01-01T00:00:01.000Z",
              payload: {
                interjection_id: "inj-classic",
                execution_id: "exec-classic",
                content: "改成用中文总结",
                status: "injected",
              },
            },
          ],
          finish_reason: "stop",
        } as NonNullable<BackendMessage["runs"]>,
      }),
    );

    expect(msg.executionId).toBeNull();
    expect(msg.runs?.events).toHaveLength(1);
    expect(msg.runs?.events[0]?.type).toBe("user_interjection");
    expect(
      useExecutionStore.getState().byId["m-classic"]?.userInterjections,
    ).toEqual([
      {
        interjectionId: "inj-classic",
        executionId: "exec-classic",
        content: "改成用中文总结",
        status: "injected",
        note: null,
      },
    ]);
  });

  it("multi-agent reload: keeps runs but does not hydrate in toMessage", () => {
    // 多 Agent 仍由 InlineTeamGraph mount 时 hydrate；toMessage 不得抢先 fold。
    const msg = toMessage(
      row({
        id: "m-team",
        role: "assistant",
        content: "团队结论…",
        status: "complete",
        runs: {
          events: [
            {
              type: "run_plan",
              timestamp: "2026-01-01T00:00:00.000Z",
              payload: {
                execution_id: "exec-team",
                plan_type: "multi_agent",
                task_summary: "调研",
                agents: [{ id: "a1", role: "研究员" }],
                runs: [
                  {
                    id: "r1",
                    agent_id: "a1",
                    task: "读文档",
                    depends_on: [],
                  },
                ],
              },
            },
            {
              type: "user_interjection",
              timestamp: "2026-01-01T00:00:01.000Z",
              payload: {
                interjection_id: "inj-team",
                execution_id: "exec-team",
                content: "补充一句",
                status: "injected",
              },
            },
          ],
          finish_reason: "stop",
        } as NonNullable<BackendMessage["runs"]>,
      }),
    );

    expect(msg.executionId).toBe("exec-team");
    expect(msg.runs?.events.length).toBe(2);
    expect(useExecutionStore.getState().byId["m-team"]).toBeUndefined();
  });

  it("surfaces pausedTurns when paused + journal cold interaction (hydrate gap)", () => {
    // Offline repro: hydrateInteractionsFromJournal alone left ResumePrompt empty.
    toMessage(
      row({
        id: "m-paused",
        role: "assistant",
        content: "checkpoint body",
        status: "running",
        paused: true,
        runs: {
          events: [
            {
              type: "plan_review_required",
              payload: {
                checkpoint_id: "pr-hydrate",
                conversation_id: "c1",
                steps: [{ run_id: "r1", role: "调研", summary: "方案就绪" }],
                pending: [{ run_id: "r2", role: "执行" }],
                ceo_review: {
                  conclusion: "方案可行，建议放行。",
                  risks: ["回滚预案缺失"],
                  suggestions: ["先灰度"],
                },
              },
            },
          ],
          finish_reason: "paused",
        } as NonNullable<BackendMessage["runs"]>,
      }),
    );

    const pending = usePausedTurnStore.getState().pending;
    expect(pending).toHaveLength(1);
    expect(pending[0]).toMatchObject({
      messageId: "m-paused",
      conversationId: "c1",
      checkpointId: "pr-hydrate",
      kind: "plan_review",
      origin: "server",
    });
    expect(pending[0].steps).toEqual([
      { run_id: "r1", role: "调研", summary: "方案就绪" },
    ]);
    // journal 冷加载同样带出把关摘要（拍板中心冷启动可见）。
    expect(pending[0].ceoReview).toEqual({
      conclusion: "方案可行，建议放行。",
      risks: ["回滚预案缺失"],
      suggestions: ["先灰度"],
    });
  });

  it("surface 画卡后清会话 isGenerating（冷挂起不变量）", () => {
    useConversationStore.getState().switchConversation("c1");
    useConversationStore.getState().addMessage(
      {
        id: "u1",
        role: "user",
        content: "q",
        createdAt: "2026-01-01T00:00:00Z",
        executionId: null,
        isStreaming: false,
      },
      "c1",
    );
    useConversationStore.getState().addMessage(
      {
        id: "m-paused",
        role: "assistant",
        content: "partial",
        createdAt: "2026-01-01T00:00:01Z",
        executionId: null,
        isStreaming: true,
        status: "running",
        serverMessageId: "m-paused",
      },
      "c1",
    );
    useConversationStore.getState().setGenerating(true, "c1");

    toMessage(
      row({
        id: "m-paused",
        role: "assistant",
        content: "checkpoint body",
        status: "running",
        paused: true,
        runs: {
          events: [
            {
              type: "checkpoint_required",
              payload: {
                checkpoint_id: "ask-h",
                conversation_id: "c1",
                question: "选哪个？",
                assumptions: [],
                questions: [],
              },
            },
          ],
          finish_reason: "paused",
        } as NonNullable<BackendMessage["runs"]>,
      }),
    );

    expect(usePausedTurnStore.getState().pending).toHaveLength(1);
    expect(usePausedTurnStore.getState().pending[0]?.kind).toBe("ask_user");
    expect(useConversationStore.getState().byId.c1?.isGenerating).toBe(false);
    expect(
      useConversationStore
        .getState()
        .byId.c1?.messages.find((m) => m.id === "m-paused")?.isStreaming,
    ).toBe(false);
  });

  it("journal cold interaction without ceo_review hydrates with no summary", () => {
    toMessage(
      row({
        id: "m-paused-no-cr",
        role: "assistant",
        content: "checkpoint body",
        status: "running",
        paused: true,
        runs: {
          events: [
            {
              type: "plan_review_required",
              payload: {
                checkpoint_id: "pr-no-cr",
                conversation_id: "c1",
                steps: [{ run_id: "r1", role: "调研", summary: "ok" }],
                pending: [],
              },
            },
          ],
          finish_reason: "paused",
        } as NonNullable<BackendMessage["runs"]>,
      }),
    );

    const pending = usePausedTurnStore.getState().pending;
    expect(pending).toHaveLength(1);
    expect(pending[0].ceoReview).toBeUndefined();
  });

  it("does not surface pausedTurns when paused without journal interactions", () => {
    toMessage(
      row({
        id: "m-paused-bare",
        role: "assistant",
        content: "a",
        status: "running",
        paused: true,
      }),
    );
    expect(usePausedTurnStore.getState().pending).toHaveLength(0);
  });

  it("maps status=incomplete to interrupted when usage/runs are not cancelled", () => {
    const msg = toMessage(
      row({
        id: "m-incomplete",
        role: "assistant",
        content: "半截",
        status: "incomplete",
      }),
    );
    expect(msg.isStreaming).toBe(false);
    expect(msg.finishReason).toBe("interrupted");
  });

  it("status=incomplete + runs.finish_reason=cancelled → cancelled, not interrupted", () => {
    const msg = toMessage(
      row({
        id: "m-cancel-runs",
        role: "assistant",
        content: "半截",
        status: "incomplete",
        runs: {
          events: [],
          finish_reason: "cancelled",
        },
      }),
    );
    expect(msg.finishReason).toBe("cancelled");
    expect(msg.finishReason).not.toBe("interrupted");
    expect(msg.isStreaming).toBe(false);
  });

  it("status=incomplete + usage.finish_reason=cancelled → cancelled even if runs.finish_reason is null", () => {
    const msg = toMessage(
      row({
        id: "m-cancel-usage",
        role: "assistant",
        content: "半截",
        status: "incomplete",
        runs: {
          events: [],
          finish_reason: null,
        },
        usage: {
          input: 0,
          output: 0,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 0,
          finish_reason: "cancelled",
        },
      }),
    );
    expect(msg.finishReason).toBe("cancelled");
    expect(msg.finishReason).not.toBe("interrupted");
  });
});
