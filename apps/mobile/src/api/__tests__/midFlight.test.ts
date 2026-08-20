import type { SSEEvent } from "@agentcore/contract-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sendMidFlightMessage } from "../midFlight";

vi.mock("@/api/client", () => ({
  apiUrl: (path: string) => `http://test${path}`,
  authHeader: () => ({ Authorization: "Bearer t" }),
  // Passthrough — midFlight tests exercise SSE/queue paths, not the 401 policy
  // (see fetchWithAuthRefresh.test.ts for replay-still-401 clearing).
  fetchWithAuthRefresh: async (doFetch: () => Promise<Response>) => doFetch(),
}));

function sseBody(events: SSEEvent[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  const text = events.map((ev) => `data: ${JSON.stringify(ev)}\n\n`).join("");
  return new ReadableStream({
    start(controller) {
      controller.enqueue(enc.encode(text));
      controller.close();
    },
  });
}

function ev(type: string, payload: Record<string, unknown> = {}): SSEEvent {
  return { type, timestamp: "", payload } as SSEEvent;
}

describe("sendMidFlightMessage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("turn_queued：仅条 ack，主路空闲后再开 turn2 并续流", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        sseBody([
          ev("turn_queued", {
            queue_id: "q1",
            position: 1,
            queue_depth: 2,
            conversation_id: "c1",
          }),
          ev("turn_queue_started", {
            queue_id: "q1",
            conversation_id: "c1",
            remaining_depth: 1,
          }),
          ev("message_start", { message_id: "m2" }),
          ev("message_end", { finish_reason: "end_turn" }),
        ]),
        {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        },
      ),
    );

    let primaryIdle = false;
    const live: string[] = [];
    const turn2: string[] = [];
    let began = 0;
    let resolveIdle!: () => void;
    const idlePromise = new Promise<void>((r) => {
      resolveIdle = r;
    });

    const queued: string[] = [];
    const pending = sendMidFlightMessage("c1", "第二问", {
      onLiveEvent: (e) => live.push(e.type),
      onQueued: (info) => {
        queued.push(info.queueId);
      },
      beginTurn2: () => {
        began += 1;
      },
      onTurn2Event: (e) => turn2.push(e.type),
      isPrimaryIdle: () => primaryIdle,
      waitPrimaryIdle: () => idlePromise,
    });

    // Allow turn_queued to land and arm the waiter before releasing primary.
    await vi.waitFor(() => expect(live).toEqual(["turn_queued"]));
    expect(queued).toEqual(["q1"]);
    expect(began).toBe(0);
    expect(turn2).toEqual([]);

    primaryIdle = true;
    resolveIdle();
    const result = await pending;

    expect(result).toEqual({
      kind: "queued",
      position: 1,
      queueDepth: 2,
      queueId: "q1",
      degradedFrom: undefined,
    });
    expect(began).toBe(1);
    expect(turn2).toEqual([
      "turn_queue_started",
      "message_start",
      "message_end",
    ]);
  });

  it("turn_queue_started 即可开 turn2（不必等 message_start）", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        sseBody([
          ev("turn_queued", {
            queue_id: "q-start",
            position: 1,
            queue_depth: 1,
            conversation_id: "c1",
          }),
          ev("turn_queue_started", {
            queue_id: "q-start",
            conversation_id: "c1",
            remaining_depth: 0,
          }),
        ]),
        {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        },
      ),
    );

    const turn2: string[] = [];
    let began = 0;
    const result = await sendMidFlightMessage("c1", "第二问", {
      onLiveEvent: () => {},
      onQueued: () => {},
      beginTurn2: () => {
        began += 1;
      },
      onTurn2Event: (e) => turn2.push(e.type),
      isPrimaryIdle: () => true,
      waitPrimaryIdle: async () => {},
    });

    expect(result.kind).toBe("queued");
    expect(began).toBe(1);
    expect(turn2).toEqual(["turn_queue_started"]);
  });
  it("POST body 带 delivery", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        sseBody([
          ev("user_interjection", {
            interjection_id: "ij1",
            execution_id: "e1",
            content: "插一句",
            status: "received",
          }),
        ]),
        {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        },
      ),
    );
    await sendMidFlightMessage(
      "c1",
      "插一句",
      {
        onLiveEvent: () => {},
        onQueued: () => {},
        beginTurn2: () => {},
        onTurn2Event: () => {},
        isPrimaryIdle: () => true,
        waitPrimaryIdle: async () => {},
      },
      undefined,
      undefined,
      "queue",
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toMatchObject({
      content: "插一句",
      delivery: "queue",
    });
  });

  it("degraded_from=steer 透出", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        sseBody([
          ev("turn_queued", {
            queue_id: "q-d",
            position: 1,
            queue_depth: 1,
            conversation_id: "c1",
            degraded_from: "steer",
          }),
        ]),
        {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        },
      ),
    );
    let queuedDegraded: string | undefined;
    const result = await sendMidFlightMessage(
      "c1",
      "x",
      {
        onLiveEvent: () => {},
        onQueued: (info) => {
          queuedDegraded = info.degradedFrom;
        },
        beginTurn2: () => {},
        onTurn2Event: () => {},
        isPrimaryIdle: () => true,
        waitPrimaryIdle: async () => {},
      },
      undefined,
      undefined,
      "steer",
    );
    expect(result).toEqual({
      kind: "queued",
      position: 1,
      queueDepth: 1,
      queueId: "q-d",
      degradedFrom: "steer",
    });
    expect(queuedDegraded).toBe("steer");
  });

  it("user_interjection status=received：即时 ack，不开 turn2", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        sseBody([
          ev("user_interjection", {
            interjection_id: "ij1",
            execution_id: "e1",
            content: "插一句",
            status: "received",
          }),
        ]),
        {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        },
      ),
    );

    const live: string[] = [];
    let began = 0;
    const result = await sendMidFlightMessage("c1", "插一句", {
      onLiveEvent: (e) => live.push(e.type),
      onQueued: () => {
        throw new Error("should not queue");
      },
      beginTurn2: () => {
        began += 1;
      },
      onTurn2Event: () => {
        throw new Error("should not turn2");
      },
      isPrimaryIdle: () => true,
      waitPrimaryIdle: async () => {},
    });

    expect(result).toEqual({ kind: "received" });
    expect(live).toEqual(["user_interjection"]);
    expect(began).toBe(0);
  });

  it("经典降级：user_interjection(queued)+turn_queued 仍 fold 插话并排队", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        sseBody([
          ev("user_interjection", {
            interjection_id: "ij-q",
            execution_id: "e1",
            content: "晚到",
            status: "received",
          }),
          ev("user_interjection", {
            interjection_id: "ij-q",
            execution_id: "e1",
            content: "晚到",
            status: "queued",
          }),
          ev("turn_queued", {
            queue_id: "q-d",
            position: 1,
            queue_depth: 1,
            conversation_id: "c1",
            degraded_from: "steer",
          }),
        ]),
        {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        },
      ),
    );

    const live: string[] = [];
    let queuedDegraded: string | undefined;
    const result = await sendMidFlightMessage(
      "c1",
      "晚到",
      {
        onLiveEvent: (e) => live.push(e.type),
        onQueued: (info) => {
          queuedDegraded = info.degradedFrom;
        },
        beginTurn2: () => {},
        onTurn2Event: () => {},
        isPrimaryIdle: () => true,
        waitPrimaryIdle: async () => {},
      },
      undefined,
      undefined,
      "steer",
    );

    expect(result).toEqual({
      kind: "queued",
      position: 1,
      queueDepth: 1,
      queueId: "q-d",
      degradedFrom: "steer",
    });
    expect(queuedDegraded).toBe("steer");
    expect(live).toEqual([
      "user_interjection",
      "user_interjection",
      "turn_queued",
    ]);
  });

  it("HTTP 202 → error（退役受理）", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ queue_id: "q" }), { status: 202 }),
    );
    const result = await sendMidFlightMessage("c1", "x", {
      onLiveEvent: () => {},
      onQueued: () => {},
      beginTurn2: () => {},
      onTurn2Event: () => {},
      isPrimaryIdle: () => true,
      waitPrimaryIdle: async () => {},
    });
    expect(result.kind).toBe("error");
  });
});
