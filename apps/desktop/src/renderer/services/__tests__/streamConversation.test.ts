import {
  StreamError,
  describeStreamError,
  errorActionForCode,
  isRetriableStreamError,
  streamErrorAction,
} from "@/lib/errors";
import { formatLocalMoment } from "@/lib/recoveryMoment";
import { captureCsrf, clearCsrfToken } from "@/services/api";
import { useConversationStore } from "@/stores/conversation";
import { MAX_RETRY_AFTER } from "@agentcore/contract-types";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as dispatchMod from "../sse/dispatch";
import {
  ATTACH_CAUGHT_UP_COMMENT,
  attachConversation,
  clearLastEventId,
  forceSseTransportDrop,
  peekLastEventId,
  pumpSseBody,
  streamConversation,
} from "../streamConversation";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
});

describe("describeStreamError", () => {
  it("surfaces the backend's verbatim message for a 429 (quota reset / cool-down)", () => {
    // 后端句子里已不含时刻（只给结构化 reset_at）；没时刻时原样透出这句。
    const err = new StreamError("http", 429, {
      code: "QUOTA_EXCEEDED",
      serverMessage:
        "已达每日 token 上限（2,000,000 / 2,000,000），额度重置后可继续。",
    });
    expect(describeStreamError(err)).toBe(
      "已达每日 token 上限（2,000,000 / 2,000,000），额度重置后可继续。",
    );
  });

  it("appends the platform gate's reset moment in the user's own timezone", () => {
    const err = new StreamError("http", 429, {
      code: "QUOTA_EXCEEDED",
      serverMessage:
        "已达每日 token 上限（2,000,000 / 2,000,000），额度重置后可继续。",
      recoveryMoment: { reset_at: "2026-08-14T16:00:00Z" },
    });
    const local = formatLocalMoment("2026-08-14T16:00:00Z");
    expect(describeStreamError(err)).toBe(
      `已达每日 token 上限（2,000,000 / 2,000,000），额度重置后可继续。额度将于 ${local} 重置。`,
    );
    expect(describeStreamError(err)).not.toContain("UTC");
  });

  it("falls back to a cool-down message for a 429 with only Retry-After", () => {
    const err = new StreamError("http", 429, {
      code: "RATE_LIMITED",
      retryAfter: 30,
    });
    expect(describeStreamError(err)).toBe("操作过于频繁，请约 30 秒后再试");
  });

  it("falls back to a generic 429 message when nothing rides along", () => {
    expect(describeStreamError(new StreamError("http", 429))).toBe(
      "操作过于频繁或额度已用尽，请稍后再试",
    );
  });

  it("surfaces the backend's actionable message for a 402 missing BYOK key", () => {
    const err = new StreamError("http", 402, {
      code: "LLM_KEY_REQUIRED",
      serverMessage: "请先接入自己的 API Key，再发起对话。",
    });
    expect(describeStreamError(err)).toBe(
      "请先接入自己的 API Key，再发起对话。",
    );
  });

  it("falls back to a config hint for a 402 with no server message", () => {
    const err = new StreamError("http", 402, { code: "LLM_KEY_REQUIRED" });
    expect(describeStreamError(err)).toContain("API Key");
    expect(describeStreamError(err)).not.toContain("服务暂时不可用");
  });

  it("phrases other http errors as a temporary outage", () => {
    expect(describeStreamError(new StreamError("http", 503))).toContain(
      "服务暂时不可用",
    );
  });

  it("maps turn_in_progress to an explicit zh wrap-up hint", () => {
    expect(
      describeStreamError(
        new StreamError("http", 409, { code: "turn_in_progress" }),
      ),
    ).toBe("回合收尾尚未完成，请稍候或先显式停止后再试");
    expect(
      describeStreamError(
        new StreamError("http", 409, {
          code: "turn_in_progress",
          serverMessage: "会话有正在进行的回合，先等它结束或显式停止",
        }),
      ),
    ).toBe("回合收尾尚未完成，请稍候或先显式停止后再试");
  });

  it("phrases network errors and stays silent on auth", () => {
    expect(describeStreamError(new StreamError("network"))).toContain("网络");
    expect(describeStreamError(new StreamError("auth"))).toBeNull();
  });

  it("maps CLIENT_TOO_OLD / 426 to force-update product copy", () => {
    expect(
      describeStreamError(
        new StreamError("http", 426, {
          code: "CLIENT_TOO_OLD",
          serverMessage: "upgrade required",
        }),
      ),
    ).toBe("桌面端版本过旧，请更新后再试");
    expect(describeStreamError(new StreamError("http", 426))).toBe(
      "桌面端版本过旧，请更新后再试",
    );
  });
});

describe("isRetriableStreamError", () => {
  it("does not offer retry for a quota refusal (resets on a schedule)", () => {
    const err = new StreamError("http", 429, { code: "QUOTA_EXCEEDED" });
    expect(isRetriableStreamError(err)).toBe(false);
  });

  it("does not offer retry for a missing BYOK key (needs configuration)", () => {
    const err = new StreamError("http", 402, { code: "LLM_KEY_REQUIRED" });
    expect(isRetriableStreamError(err)).toBe(false);
  });

  it("offers retry for rate-limit, transport, and unknown errors", () => {
    expect(
      isRetriableStreamError(
        new StreamError("http", 429, { code: "RATE_LIMITED" }),
      ),
    ).toBe(true);
    expect(isRetriableStreamError(new StreamError("network"))).toBe(true);
    expect(isRetriableStreamError(new Error("boom"))).toBe(true);
  });

  it("suppresses retry when attested retry_after exceeds the shared ceiling", () => {
    expect(
      isRetriableStreamError(
        new StreamError("http", 429, {
          code: "RATE_LIMITED",
          retryAfter: MAX_RETRY_AFTER + 1,
        }),
      ),
    ).toBe(false);
    expect(
      isRetriableStreamError(
        new StreamError("http", 429, {
          code: "LLM_RATE_LIMIT",
          retryAfter: 57_600,
        }),
      ),
    ).toBe(false);
  });

  it("still offers retry at the ceiling and below", () => {
    expect(
      isRetriableStreamError(
        new StreamError("http", 429, {
          code: "RATE_LIMITED",
          retryAfter: MAX_RETRY_AFTER,
        }),
      ),
    ).toBe(true);
    expect(
      isRetriableStreamError(
        new StreamError("http", 429, {
          code: "LLM_RATE_LIMIT",
          retryAfter: 4,
        }),
      ),
    ).toBe(true);
  });
});

describe("errorActionForCode", () => {
  it("routes missing and invalid keys to the providers page", () => {
    expect(errorActionForCode("LLM_KEY_REQUIRED")).toEqual({
      label: "去服务商",
      href: "/more/providers",
    });
    expect(errorActionForCode("LLM_KEY_INVALID")).toEqual({
      label: "去服务商",
      href: "/more/providers",
    });
  });

  it("routes FREE_TIER_EXHAUSTED as unknown code (no settings CTA)", () => {
    // FREE_TIER_EXHAUSTED retired with the free-tier path; leftover wire codes
    // fall through to null action (quota uses QUOTA_EXCEEDED).
    expect(errorActionForCode("FREE_TIER_EXHAUSTED")).toBeNull();
  });

  it("routes balance errors to settings; quota offers a BYOK secondary exit (F6)", () => {
    expect(errorActionForCode("LLM_INSUFFICIENT_BALANCE")).toEqual({
      label: "去服务商",
      href: "/more/providers",
    });
    // 平台额度耗尽补次级 CTA「接入自己的 Key」(成本配额与计费 §〇·六 F6).
    expect(errorActionForCode("QUOTA_EXCEEDED")).toEqual({
      label: "接入自己的 Key",
      href: "/more/providers",
    });
    expect(errorActionForCode(undefined)).toBeNull();
  });

  it("streamErrorAction delegates to the code map for a StreamError", () => {
    expect(
      streamErrorAction(
        new StreamError("http", 402, { code: "LLM_KEY_REQUIRED" }),
      ),
    ).toEqual({ label: "去服务商", href: "/more/providers" });
    expect(streamErrorAction(new Error("boom"))).toBeNull();
  });
});

describe("streamConversation (refused turn)", () => {
  it("parses a 429 JSON body + Retry-After header into a StreamError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: "RATE_LIMITED",
                message: "操作过于频繁，请约 42 秒后再发送。",
                reset_at: "2026-08-14T16:00:00Z",
              },
            }),
            {
              status: 429,
              headers: {
                "Content-Type": "application/json",
                "Retry-After": "42",
              },
            },
          ),
        ),
      ),
    );

    const fetchMock = vi.mocked(fetch);
    const err = await streamConversation({
      conversationId: "c1",
      content: "hi",
      attachments: [],
      delivery: "steer",
    }).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(StreamError);
    const streamErr = err as StreamError;
    expect(streamErr.status).toBe(429);
    expect(streamErr.code).toBe("RATE_LIMITED");
    expect(streamErr.serverMessage).toBe("操作过于频繁，请约 42 秒后再发送。");
    expect(streamErr.retryAfter).toBe(42);
    // 结构化时刻原样收下（ISO8601 UTC），成文留给渲染层按本机时区做。
    expect(streamErr.recoveryMoment?.reset_at).toBe("2026-08-14T16:00:00Z");
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toEqual(
      expect.objectContaining({
        "X-Client-Platform": expect.any(String),
        "X-Client-Version": expect.any(String),
      }),
    );
  });

  it("sets turnCommit.committed when this send's pump sees turn_saved", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            'data: {"type":"turn_saved","timestamp":"t","payload":{"user_message_id":"u1"}}\n\n',
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          ),
        ),
      ),
    );
    const turnCommit = { committed: false };
    await streamConversation({
      conversationId: "c1",
      content: "hi",
      delivery: "steer",
      turnCommit,
    });
    expect(turnCommit.committed).toBe(true);
  });

  it("leaves turnCommit.committed false when the pump never sees turn_saved", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            'data: {"type":"message_end","timestamp":"t","payload":{"finish_reason":"end_turn"}}\n\n',
            { status: 200, headers: { "Content-Type": "text/event-stream" } },
          ),
        ),
      ),
    );
    const turnCommit = { committed: false };
    await streamConversation({
      conversationId: "c1",
      content: "hi",
      delivery: "steer",
      turnCommit,
    });
    expect(turnCommit.committed).toBe(false);
  });
});

/**
 * 起回合的 POST 也吃 CSRF 403 自愈（与 `api.request` / `authedFetch` 同一条判据）。
 * 缺这条时的形状：离线转在线后会话半武装，读请求全通、写请求全 403，用户点发送「没反应」。
 *
 * 「重发一条会起回合的 POST」之所以不是隐患，全由 `isReplayableCsrfRejection` 保证——
 * 它只在 middleware 前置拒绝、handler 从未执行、且服务端回发了新令牌时才为真。故这里
 * 两条用例分别钉住「回发了令牌 → 重放一次且带新令牌」与「没回发 → 只发一次、原样失败」。
 */
describe("streamConversation (CSRF 403 自愈)", () => {
  /** 后端 middleware/csrf.py 的拒绝体，抵达客户端时的样子。 */
  const CSRF_BODY = JSON.stringify({
    error: {
      code: "CSRF_FAILED",
      message: "CSRF token missing or invalid. Re-login and retry.",
    },
  });

  /** 一次被拒的写；`reissued` = 这次拒绝随手回发的替换令牌。 */
  const csrfRejection = (reissued?: string): Response =>
    new Response(CSRF_BODY, {
      status: 403,
      headers: {
        "Content-Type": "application/json",
        ...(reissued ? { "X-CSRF-Token": reissued } : {}),
      },
    });

  /** 一条立即收口的 SSE 流（重放成功后要真的被当流读完）。 */
  const sseTurn = (): Response =>
    new Response(
      'data: {"type":"message_end","timestamp":"t","payload":{"finish_reason":"end_turn"}}\n\n',
      { status: 200, headers: { "Content-Type": "text/event-stream" } },
    );

  /**
   * 按序回放脚本响应；`/v1/auth/refresh` 单独作答，让真实的 `tryRefresh`（及它带的
   * 令牌轮换）照跑。`sentTokens` 记录每次发往回合端点时实际带上的 `X-CSRF-Token`，
   * 长度即预算所约束的尝试次数；脚本用尽仍再发 = 重放成环，直接炸。
   */
  function stubFetch(
    responses: Response[],
    refresh?: () => Response,
  ): { sentTokens: (string | undefined)[] } {
    const queue = [...responses];
    const sentTokens: (string | undefined)[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: unknown, init?: RequestInit) => {
        if (String(input).includes("/v1/auth/refresh")) {
          return Promise.resolve(
            refresh?.() ?? new Response(null, { status: 503 }),
          );
        }
        const headers = (init?.headers ?? {}) as Record<string, string>;
        sentTokens.push(headers["X-CSRF-Token"]);
        const next = queue.shift();
        if (!next)
          throw new Error(`第 ${sentTokens.length} 次发送：重放成环了`);
        return Promise.resolve(next);
      }),
    );
    return { sentTokens };
  }

  const send = () =>
    streamConversation({
      conversationId: "c1",
      content: "hi",
      delivery: "steer",
    });

  afterEach(() => {
    clearCsrfToken();
  });

  it("可自愈的 403 重放一次，第二次带上服务端回发的新令牌", async () => {
    vi.spyOn(dispatchMod, "dispatchSSEEvent").mockImplementation(() => {});
    captureCsrf(
      new Response(null, { headers: { "X-CSRF-Token": "tok-stale" } }),
    );
    const { sentTokens } = stubFetch([
      csrfRejection("tok-reissued"),
      sseTurn(),
    ]);

    await expect(send()).resolves.toBeUndefined();

    // doFetch 每次调用重算 header——重放靠这个才带得上刚换发的令牌。
    expect(sentTokens).toEqual(["tok-stale", "tok-reissued"]);
  });

  it("没回发令牌的 403 只发一次，原样失败", async () => {
    // 无 header = 服务端刻意不重新武装（呈上的令牌签给了别的会话），重发只会以
    // *那个*会话的身份起回合，所以必须保持失败。
    const { sentTokens } = stubFetch([csrfRejection()]);

    const err = await send().catch((e: unknown) => e);

    expect(sentTokens).toHaveLength(1);
    expect(err).toBeInstanceOf(StreamError);
    expect((err as StreamError).status).toBe(403);
    expect((err as StreamError).code).toBe("CSRF_FAILED");
  });

  it("与 401 刷新重放共用一份预算（一次调用最多发两次）", async () => {
    // 刷新成功后的重放又吃到可自愈 403：再重放就是第三次发送，共享预算不允许。
    const { sentTokens } = stubFetch(
      [new Response(null, { status: 401 }), csrfRejection("tok-late")],
      () =>
        new Response(null, {
          status: 200,
          headers: { "X-CSRF-Token": "tok-rotated" },
        }),
    );

    const err = await send().catch((e: unknown) => e);

    expect((err as StreamError).status).toBe(403);
    expect(sentTokens).toEqual([undefined, "tok-rotated"]);
  });
});

describe("attachConversation (实时重连续看 1b)", () => {
  it("returns 'none' on a 204 so the caller falls back to the persisted transcript", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(null, { status: 204 }))),
    );
    useConversationStore.getState().switchConversation("c1");
    await expect(attachConversation("c1")).resolves.toBe("none");
  });

  it("targets the conversation's stream endpoint with a GET", async () => {
    const fetchMock = vi.fn((_input: string, _init?: RequestInit) =>
      Promise.resolve(new Response(null, { status: 204 })),
    );
    vi.stubGlobal("fetch", fetchMock);
    useConversationStore.getState().switchConversation("conv-42");
    await attachConversation("conv-42");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/v1/conversations/conv-42/stream");
    // 回合级语义：绝不带 ``follow``——rejoinLiveTurn 的「无 live run → 读持久化」
    // 分支就靠这个 204（对话级长订阅走 turns/conversationFollow）。
    expect(url).not.toContain("follow");
    expect(init?.method).toBe("GET");
    expect(init?.headers).toEqual(
      expect.objectContaining({
        "X-Client-Platform": expect.any(String),
        "X-Client-Version": expect.any(String),
      }),
    );
  });

  it("raises a StreamError when the attach is refused (e.g. not owned → 404)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ error: { code: "NOT_FOUND" } }), {
            status: 404,
            headers: { "Content-Type": "application/json" },
          }),
        ),
      ),
    );
    useConversationStore.getState().switchConversation("c1");
    const err = await attachConversation("c1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(StreamError);
    expect((err as StreamError).status).toBe(404);
  });

  it("buffers replay until attach-caught-up, then delivers live events", async () => {
    const seen: string[] = [];
    vi.spyOn(dispatchMod, "dispatchSSEEvent").mockImplementation((event) => {
      seen.push(event.type);
      if (event.type === "message_end") {
        useConversationStore.getState().setGenerating(false, "c1");
      }
    });
    vi.spyOn(dispatchMod, "flushPendingContent").mockImplementation(() => {});
    vi.spyOn(dispatchMod, "flushPendingFrames").mockImplementation(() => {});

    const body = [
      'data: {"type":"run_started","timestamp":"t","payload":{"run_id":"w1","agent_id":"a","kind":"agent"}}\n\n',
      'data: {"type":"run_completed","timestamp":"t","payload":{"run_id":"w1","agent_id":"a"}}\n\n',
      `: ${ATTACH_CAUGHT_UP_COMMENT}\n\n`,
      'data: {"type":"run_output_delta","timestamp":"t","payload":{"run_id":"w2","agent_id":"b","delta":"x"}}\n\n',
      'data: {"type":"message_end","timestamp":"t","payload":{"finish_reason":"end_turn"}}\n\n',
    ].join("");

    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(body, {
            status: 200,
            headers: { "Content-Type": "text/event-stream" },
          }),
        ),
      ),
    );
    useConversationStore.getState().switchConversation("c1");
    useConversationStore.getState().createAssistantMessage("c1");

    await expect(attachConversation("c1")).resolves.toBe("attached");
    expect(seen).toEqual([
      "run_started",
      "run_completed",
      "run_output_delta",
      "message_end",
    ]);
  });
});

describe("pumpSseBody comments", () => {
  it("surfaces attach-caught-up (and ignores unknown comment text shape)", async () => {
    const events: string[] = [];
    const comments: string[] = [];
    const body = [
      'data: {"type":"content_delta","timestamp":"t","payload":{"delta":"a"}}\n\n',
      ": ping\n\n",
      `: ${ATTACH_CAUGHT_UP_COMMENT}\n\n`,
      'data: {"type":"content_delta","timestamp":"t","payload":{"delta":"b"}}\n\n',
    ].join("");
    await pumpSseBody(
      new Response(body, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
      "c1",
      (e) => events.push(e.type),
      (c) => comments.push(c),
    );
    expect(events).toEqual(["content_delta", "content_delta"]);
    expect(comments).toEqual(["ping", ATTACH_CAUGHT_UP_COMMENT]);
  });

  it("forceSseTransportDrop rejects the active pump as StreamError network", async () => {
    let pull!: (chunk: Uint8Array | null) => void;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        pull = (chunk) => {
          if (chunk == null) controller.close();
          else controller.enqueue(chunk);
        };
      },
    });
    const pumped = pumpSseBody(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
      "force-drop-cid",
      () => {},
    );
    // Let the first readChunk park on reader.read().
    await Promise.resolve();
    expect(forceSseTransportDrop("force-drop-cid")).toBe(true);
    await expect(pumped).rejects.toMatchObject({
      name: "StreamError",
      kind: "network",
    });
    expect(forceSseTransportDrop("force-drop-cid")).toBe(false);
    // Avoid hanging the ReadableStream if anything still holds it.
    try {
      pull(null);
    } catch {
      /* already cancelled */
    }
  });

  it("does not advance Last-Event-ID until the event is dispatched", async () => {
    clearLastEventId("c-cursor");
    const body =
      'id: 7\ndata: {"type":"content_delta","timestamp":"t","payload":{"delta":"a"}}\n\n';
    await pumpSseBody(
      new Response(body, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
      "c-cursor",
      () => {
        expect(peekLastEventId("c-cursor")).toBeUndefined();
      },
    );
    // Custom onEvent did not fold — cursor stays put (丢未折段不清).
    expect(peekLastEventId("c-cursor")).toBeUndefined();
  });

  it("default pump commits Last-Event-ID after dispatch", async () => {
    clearLastEventId("c-commit");
    vi.spyOn(dispatchMod, "dispatchSSEEvent").mockImplementation(() => {});
    const body =
      'id: 9\ndata: {"type":"content_delta","timestamp":"t","payload":{"delta":"a"}}\n\n';
    await pumpSseBody(
      new Response(body, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
      "c-commit",
    );
    expect(peekLastEventId("c-commit")).toBe("9");
    clearLastEventId("c-commit");
  });
});

describe("pumpSseBody X-AgentCore-Trace", () => {
  const TRACE = "a".repeat(32);
  const OTHER = "b".repeat(32);
  const CID = "c-trace";

  function lastOf() {
    const rt = useConversationStore.getState().byId[CID];
    return rt?.messages[rt.messages.length - 1];
  }

  function pumpWithHeader(header?: string) {
    const headers: Record<string, string> = {
      "Content-Type": "text/event-stream",
    };
    if (header !== undefined) headers["X-AgentCore-Trace"] = header;
    return pumpSseBody(
      new Response("", { status: 200, headers }),
      CID,
      () => {},
    );
  }

  it("stamps last assistant when the header is 32-hex", async () => {
    useConversationStore.getState().switchConversation(CID);
    useConversationStore.getState().createAssistantMessage(CID);
    await pumpWithHeader(TRACE);
    expect(lastOf()?.role).toBe("assistant");
    expect(lastOf()?.traceId).toBe(TRACE);
  });

  it("stashes when there is no assistant and createAssistantMessage applies it", async () => {
    useConversationStore.getState().switchConversation(CID);
    await pumpWithHeader(TRACE);
    expect(useConversationStore.getState().byId[CID]?.messages).toEqual([]);
    expect(useConversationStore.getState().byId[CID]?.pendingTraceId).toBe(
      TRACE,
    );
    useConversationStore.getState().createAssistantMessage(CID);
    expect(lastOf()?.traceId).toBe(TRACE);
    expect(
      useConversationStore.getState().byId[CID]?.pendingTraceId,
    ).toBeNull();
  });

  it("does not overwrite an existing non-empty traceId", async () => {
    useConversationStore.getState().switchConversation(CID);
    useConversationStore.getState().createAssistantMessage(CID);
    useConversationStore.getState().setTraceIdOnLastMessage(TRACE, CID);
    await pumpWithHeader(OTHER);
    expect(lastOf()?.traceId).toBe(TRACE);
    expect(useConversationStore.getState().byId[CID]?.pendingTraceId).toBe(
      OTHER,
    );
  });

  it("stashes when last assistant is a completed previous turn", async () => {
    useConversationStore.getState().switchConversation(CID);
    useConversationStore.getState().createAssistantMessage(CID);
    useConversationStore.getState().setTraceIdOnLastMessage(TRACE, CID);
    useConversationStore.getState().finalizeLastMessage(CID);
    await pumpWithHeader(OTHER);
    expect(lastOf()?.traceId).toBe(TRACE);
    expect(useConversationStore.getState().byId[CID]?.pendingTraceId).toBe(
      OTHER,
    );
    useConversationStore.getState().createAssistantMessage(CID);
    expect(lastOf()?.traceId).toBe(OTHER);
    expect(
      useConversationStore.getState().byId[CID]?.pendingTraceId,
    ).toBeNull();
  });

  it("keeps pending when stamp hits an already-traced streaming bubble", async () => {
    useConversationStore.getState().switchConversation(CID);
    useConversationStore.getState().createAssistantMessage(CID);
    useConversationStore.getState().setTraceIdOnLastMessage(TRACE, CID);
    await pumpWithHeader(OTHER);
    useConversationStore.getState().stampPendingTraceId(CID);
    expect(lastOf()?.traceId).toBe(TRACE);
    expect(useConversationStore.getState().byId[CID]?.pendingTraceId).toBe(
      OTHER,
    );
  });

  it("ignores invalid or empty headers", async () => {
    useConversationStore.getState().switchConversation(CID);
    await pumpWithHeader("not-a-trace");
    expect(
      useConversationStore.getState().byId[CID]?.pendingTraceId,
    ).toBeNull();
    useConversationStore.getState().createAssistantMessage(CID);
    expect(lastOf()?.traceId).toBeUndefined();
    await pumpWithHeader("");
    expect(lastOf()?.traceId).toBeUndefined();
    await pumpWithHeader("g".repeat(32));
    expect(lastOf()?.traceId).toBeUndefined();
    await pumpWithHeader("a".repeat(31));
    expect(lastOf()?.traceId).toBeUndefined();
    await pumpWithHeader("aabbccdd-eeff-0011-2233-445566778899");
    expect(lastOf()?.traceId).toBeUndefined();
    await pumpWithHeader();
    expect(lastOf()?.traceId).toBeUndefined();
    expect(
      useConversationStore.getState().byId[CID]?.pendingTraceId,
    ).toBeNull();
  });
});
