import { describe, expect, it } from "vitest";
import {
  assertFoldSource,
  foldEventsFrom,
  openEventDocument,
  prepareFoldSource,
} from "../source";

describe("preview source adapter (consumer A / FOLD)", () => {
  it("normalizes legacy kind/ts dialect on turn fixtures", () => {
    const doc = openEventDocument({
      name: "legacy",
      events: [
        {
          kind: "content_delta",
          payload: { delta: "hi" },
          ts: "2026-01-01T00:00:00.000Z",
        },
      ],
      projected: { status: "completed" },
    });
    expect(doc.kind).toBe("turn_fixture");
    expect(doc.events[0]?.type).toBe("content_delta");
    expect(doc.events[0]?.timestamp).toBe("2026-01-01T00:00:00.000Z");
    expect(doc.hasPacing).toBe(false);
  });

  it("opens tape documents and keeps t_ms (pacing ignored at fold)", () => {
    const doc = openEventDocument({
      version: 1,
      meta: { title: "demo tape" },
      events: [
        {
          kind: "run_started",
          payload: { run_id: "c1", kind: "captain" },
          ts: "2026-01-01T00:00:00.000Z",
          t_ms: 0,
        },
      ],
    });
    expect(doc.kind).toBe("tape");
    expect(doc.hasPacing).toBe(true);
    expect((doc.events[0] as { t_ms?: number }).t_ms).toBe(0);
    expect(doc.name).toBe("demo tape");
  });

  it("stitches recording segments", () => {
    const doc = openEventDocument({
      kind: "demo_tape_recording",
      segments: [
        {
          events: [{ type: "message_start", payload: {}, timestamp: null }],
        },
        {
          events: [
            {
              kind: "content_delta",
              payload: { delta: "a" },
              ts: null,
              t_ms: 5,
            },
          ],
        },
      ],
    });
    expect(doc.kind).toBe("recording");
    expect(doc.events).toHaveLength(2);
    expect(doc.events[1]?.type).toBe("content_delta");
  });

  it("prepareFoldSource never remints interaction ids", () => {
    const source = prepareFoldSource({
      name: "fx",
      events: [
        {
          type: "plan_review_required",
          payload: { checkpoint_id: "cp-fixed" },
          timestamp: null,
        },
      ],
      projected: { status: "paused" },
    });
    expect(source.consumer).toBe("fold");
    expect(source.events[0]?.payload).toMatchObject({
      checkpoint_id: "cp-fixed",
    });
  });

  it("assertFoldSource rejects sink consumer at the API boundary", () => {
    expect(() => assertFoldSource({ consumer: "sink" })).toThrow(
      /mutual exclusion/,
    );
  });

  it("foldEventsFrom accepts SSEEvent[] and FoldReplaySource", () => {
    const events = [
      {
        type: "content_delta" as const,
        payload: { delta: "x" },
        timestamp: null,
      },
    ];
    expect(foldEventsFrom(events)).toHaveLength(1);
    const source = prepareFoldSource({ events });
    expect(foldEventsFrom(source)).toEqual(source.events);
  });
});
