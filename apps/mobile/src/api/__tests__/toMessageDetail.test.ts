import { toMessageDetail } from "@/api/conversations";
import { resolveEmptyFailureNotice } from "@/lib/errors";
import {
  exportDeliverableText,
  formatMessageExport,
} from "@/lib/messageExport";
import type { components } from "@/types/api.generated";
import { describe, expect, it } from "vitest";

type Row = components["schemas"]["MessageDetail"];

function baseRow(over: Partial<Row> = {}): Row {
  return {
    id: "m1",
    role: "assistant",
    content: "见 #r1",
    reasoning_content: null,
    conversation_id: "c1",
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

describe("toMessageDetail evidence_ledger", () => {
  it("maps REST evidence_ledger onto MessageDetail.evidenceLedger", () => {
    const m = toMessageDetail(
      baseRow({
        evidence_ledger: [
          {
            id: "#r1",
            url: "https://example.com/a",
            title: "A",
            snippet: "snip",
            site: "example.com",
            date: "2026-01-01",
            tier: "media",
            query: "q",
            deep_read: false,
            registrant: "ceo",
            citable: true,
            selected: false,
            doc_kind: "",
          },
        ],
      }),
    );
    expect(m.evidenceLedger).toEqual([
      expect.objectContaining({ id: "#r1", title: "A", site: "example.com" }),
    ]);
  });

  it("omits evidenceLedger when REST column is empty", () => {
    expect(
      toMessageDetail(baseRow({ evidence_ledger: [] })).evidenceLedger,
    ).toBeUndefined();
    expect(toMessageDetail(baseRow()).evidenceLedger).toBeUndefined();
  });

  it("maps REST trace_id onto MessageDetail.trace_id", () => {
    const tid = "b".repeat(32);
    expect(toMessageDetail(baseRow({ trace_id: tid })).trace_id).toBe(tid);
    expect(toMessageDetail(baseRow()).trace_id).toBeNull();
  });

  it("carries the 曾中断恢复 marker (崩溃重驱归属原回合)", () => {
    expect(toMessageDetail(baseRow({ recovered: true })).recovered).toBe(true);
    expect(toMessageDetail(baseRow()).recovered).toBeNull();
  });

  it("maps REST agent_mentions onto history user-bubble chips", () => {
    const m = toMessageDetail(
      baseRow({
        role: "user",
        content: "帮我调研",
        agent_mentions: [{ agent_id: "w1", role: "研究员" }],
      }),
    );
    expect(m.agentMentions).toEqual([{ agentId: "w1", role: "研究员" }]);
  });
});

describe("toMessageDetail runs.error (cold-load failure)", () => {
  it("projects runs.error so ChatPage can show a specific failure line", () => {
    const m = toMessageDetail(
      baseRow({
        content: "",
        runs: {
          events: [],
          finish_reason: "error",
          process: null,
          error: {
            code: "LLM_KEY_INVALID",
            message: "API Key 已吊销，请重新配置。",
          },
        },
      }),
    );
    expect(m.runs?.error).toEqual({
      code: "LLM_KEY_INVALID",
      message: "API Key 已吊销，请重新配置。",
    });
    expect(
      resolveEmptyFailureNotice({
        content: m.content,
        finishReason: m.runs?.finish_reason,
        errorMessage: m.runs?.error?.message,
      }),
    ).toBe("API Key 已吊销，请重新配置。");
  });

  it("maps REST outcome so CEO pause hydrates without waiting on journal", () => {
    const m = toMessageDetail(
      baseRow({
        paused: true,
        outcome: "paused",
        runs: {
          events: [],
          finish_reason: "paused",
          process: null,
          error: {
            code: "LLM_RATE_LIMIT",
            message: "上游限流，暂时无法继续本回合。",
          },
        },
      }),
    );
    expect(m.outcome).toBe("paused");
    expect(m.paused).toBe(true);
  });

  it("keeps null error on clean turns", () => {
    const m = toMessageDetail(
      baseRow({
        runs: {
          events: [],
          finish_reason: "end_turn",
          process: null,
          error: null,
        },
      }),
    );
    expect(m.runs?.error).toBeNull();
  });

  it("still lifts runs.auto_folder from REST (event projection; chat no longer renders it)", () => {
    const m = toMessageDetail(
      baseRow({
        runs: {
          events: [],
          finish_reason: "end_turn",
          process: null,
          auto_folder: { folder_id: "f1", name: "季度复盘" },
        },
      }),
    );
    expect(m.runs?.auto_folder).toEqual({
      folder_id: "f1",
      name: "季度复盘",
    });
  });

  it("status=incomplete + runs.finish_reason=cancelled → cancelled, not interrupted", () => {
    const m = toMessageDetail(
      baseRow({
        content: "半截",
        status: "incomplete",
        runs: {
          events: [],
          finish_reason: "cancelled",
          process: null,
        },
      }),
    );
    expect(m.runs?.finish_reason).toBe("cancelled");
  });

  it("status=incomplete + usage.finish_reason=cancelled → cancelled even if runs is null", () => {
    const m = toMessageDetail(
      baseRow({
        content: "半截",
        status: "incomplete",
        usage: {
          input: 0,
          output: 0,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 0,
          finish_reason: "cancelled",
        } as Row["usage"],
      }),
    );
    expect(m.runs?.finish_reason).toBe("cancelled");
  });
});

describe("cold-load empty failure → export", () => {
  it("exports the structured error when content is empty", () => {
    const m = toMessageDetail(
      baseRow({
        content: "",
        runs: {
          events: [],
          finish_reason: "error",
          process: null,
          error: { code: "LLM_TIMEOUT", message: "上游超时，请稍后重试。" },
        },
      }),
    );
    const notice = resolveEmptyFailureNotice({
      content: m.content,
      finishReason: m.runs?.finish_reason,
      errorMessage: m.runs?.error?.message,
    });
    expect(exportDeliverableText(m.content, notice)).toBe(
      "上游超时，请稍后重试。",
    );
    expect(
      formatMessageExport(m.content ?? "", undefined, "deliverable", {
        failureNotice: notice,
      }),
    ).toBe("上游超时，请稍后重试。");
  });
});
