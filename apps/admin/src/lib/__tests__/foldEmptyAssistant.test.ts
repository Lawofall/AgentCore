import { foldEmptyAssistantFollowers } from "@/lib/foldEmptyAssistant";
import type { ReplayMessage } from "@/services/adminObservability";
import { describe, expect, it } from "vitest";

function msg(
  p: Partial<ReplayMessage> & { id: string; role: string },
): ReplayMessage {
  return {
    content: null,
    cost_total: 0,
    created_at: "2026-08-01T00:00:00Z",
    credential_source: null,
    harvest_kind: null,
    metrics: null,
    models: [],
    origin: null,
    runs: [],
    runs_payload: null,
    projected: null,
    has_final_state: false,
    spans: [],
    trace_id: null,
    ...p,
  };
}

describe("foldEmptyAssistantFollowers", () => {
  it("merges a blank assistant into the preceding assistant", () => {
    const tree = { runs: [{ id: "r1", role: "captain", status: "completed" }] };
    const { messages, shownIdFor } = foldEmptyAssistantFollowers([
      msg({ id: "u1", role: "user", content: "审计 worker" }),
      msg({
        id: "a1",
        role: "assistant",
        content: "核心发现如下。",
        has_final_state: true,
      }),
      msg({
        id: "a2",
        role: "assistant",
        content: "",
        has_final_state: true,
        projected: tree,
      }),
    ]);

    expect(messages.map((m) => m.id)).toEqual(["u1", "a1"]);
    expect(messages[1]?.content).toBe("核心发现如下。");
    expect(messages[1]?.projected).toEqual(tree);
    expect(shownIdFor("a2")).toBe("a1");
    expect(shownIdFor("a1")).toBe("a1");
  });

  it("does not fold an empty assistant that follows a user turn", () => {
    const { messages } = foldEmptyAssistantFollowers([
      msg({ id: "u1", role: "user", content: "继续" }),
      msg({ id: "a1", role: "assistant", content: "", has_final_state: true }),
    ]);
    expect(messages.map((m) => m.id)).toEqual(["u1", "a1"]);
  });

  it("does not fold an assistant with only reasoning_content", () => {
    const { messages } = foldEmptyAssistantFollowers([
      msg({ id: "a1", role: "assistant", content: "第一回合" }),
      msg({
        id: "a2",
        role: "assistant",
        content: "",
        reasoning_content: "纯思考",
      }),
    ]);
    expect(messages.map((m) => m.id)).toEqual(["a1", "a2"]);
  });

  it("keeps reasoning_content when folding a blank follower", () => {
    const { messages } = foldEmptyAssistantFollowers([
      msg({
        id: "a1",
        role: "assistant",
        content: "正文",
        reasoning_content: "先想",
      }),
      msg({
        id: "a2",
        role: "assistant",
        content: "",
        has_final_state: true,
        projected: { runs: [] },
      }),
    ]);
    expect(messages).toHaveLength(1);
    expect(messages[0]?.reasoning_content).toBe("先想");
  });

  it("does not fold two assistants that both have body text", () => {
    const { messages } = foldEmptyAssistantFollowers([
      msg({ id: "a1", role: "assistant", content: "第一回合" }),
      msg({ id: "a2", role: "assistant", content: "第二回合" }),
    ]);
    expect(messages.map((m) => m.id)).toEqual(["a1", "a2"]);
  });
});
