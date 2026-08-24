import { resolveChatTurn } from "@/components/chat/chatTurn";
import { describe, expect, it } from "vitest";

describe("resolveChatTurn", () => {
  it("prefers projected.process when runs_payload.process is an empty array", () => {
    const turn = resolveChatTurn({
      content: "x",
      projected: {
        status: "completed",
        process: [{ kind: "reasoning", text: "完整思考" }],
      },
      runsPayload: { process: [] },
    });
    expect(turn.process).toEqual([{ kind: "reasoning", text: "完整思考" }]);
  });

  it("falls back to runs_payload.process only when projected is null", () => {
    const turn = resolveChatTurn({
      content: "x",
      projected: null,
      runsPayload: {
        process: [{ kind: "tool", tool_name: "web_search", status: "success" }],
      },
    });
    expect(turn.process).toHaveLength(1);
    expect(turn.process[0]).toMatchObject({ kind: "tool" });
  });

  it("keeps per-run process on projected.runs", () => {
    const turn = resolveChatTurn({
      content: "CEO",
      projected: {
        runs: [
          {
            id: "r1",
            role: "调研员",
            process: [
              { kind: "reasoning", text: "先搜。" },
              {
                kind: "tool",
                id: "t1",
                tool_name: "web_search",
                status: "success",
              },
            ],
          },
        ],
      },
    });
    expect(turn.runs[0]?.process).toHaveLength(2);
    expect(turn.runs[0]?.process[1]).toMatchObject({ kind: "tool" });
  });

  it("treats missing nested projected fields as empty, not throw", () => {
    const turn = resolveChatTurn({
      content: "hi",
      projected: { status: "completed" },
    });
    expect(turn.citations).toEqual([]);
    expect(turn.runs).toEqual([]);
    expect(turn.interactions).toEqual([]);
    expect(turn.process).toEqual([]);
    expect(turn.progress).toEqual({ completed: 0, total: 0 });
  });

  it("falls back to reasoningContent when projected has no reasoning", () => {
    const turn = resolveChatTurn({
      content: "答复",
      reasoningContent: "列表里的思考列",
    });
    expect(turn.reasoning).toBe("列表里的思考列");
  });

  it("prefers projected.reasoning over reasoningContent", () => {
    const turn = resolveChatTurn({
      content: "答复",
      reasoningContent: "列表思考",
      projected: { reasoning: "终态思考" },
    });
    expect(turn.reasoning).toBe("终态思考");
  });

  it("keeps ask_user.question from the projected leaf", () => {
    const turn = resolveChatTurn({
      content: "x",
      projected: {
        interactions: [
          {
            kind: "ask_user",
            id: "cp1",
            status: "resolved",
            question: "先做官网还是先做品牌？",
          },
        ],
      },
    });
    expect(turn.interactions[0]).toMatchObject({
      kind: "ask_user",
      id: "cp1",
      status: "resolved",
      question: "先做官网还是先做品牌？",
    });
  });
});
