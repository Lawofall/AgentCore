/**
 * `#/preview` 回放 source 适配器（录制回放通用化提案 步③ · 消费端 A）。
 *
 * 读同一超集事件文档（conformance 向量 / 磁带 / 录制原片），准备为 **FOLD**
 * 直灌源。缺 pacing（`t_ms`）则等步忽略——帧滑块仍按事件条数。
 *
 * A/B 互斥在类型与运行时双重表达：本模块**只**产出 `FoldReplaySource`；
 * 不提供 SINK 准备入口（B 路在服务端 `agentcore.replay`）。禁止把 sink 源
 * 再直灌 fold（会双份 fold 同一流）。
 *
 * 身份重铸：FOLD 路永不 remint（巡检 golden / 深链依赖稳定 id）。
 */

import type { SSEEvent } from "@/types/events";
import { isTurnFixture } from "@agentcore/protocol-conformance/fixtureKind";

export type ConsumerKind = "fold" | "sink";

export type DocumentKind =
  | "turn_fixture"
  | "tape"
  | "recording"
  | "bare_events";

export interface EventDocument {
  kind: DocumentKind;
  events: SSEEvent[];
  name?: string;
  description?: string;
  hasPacing: boolean;
}

declare const foldBrand: unique symbol;

/** 仅 A 路（fold 直灌）可用的已准备源——类型上排除 sink。 */
export interface FoldReplaySource {
  readonly [foldBrand]: true;
  readonly consumer: "fold";
  events: SSEEvent[];
  name?: string;
  documentKind: DocumentKind;
  hasPacing: boolean;
}

function eventType(ev: Record<string, unknown>): string {
  return String(ev.type ?? "");
}

function eventTimestamp(ev: Record<string, unknown>): string | null {
  if (typeof ev.timestamp === "string") return ev.timestamp;
  return null;
}

/** 归一单条事件到契约字段；保留 `t_ms` 等超集（FOLD 忽略等步）。 */
export function normalizeReplayEvent(raw: unknown): SSEEvent {
  if (typeof raw !== "object" || raw === null) {
    throw new Error(`replay event must be an object, got ${typeof raw}`);
  }
  const ev = raw as Record<string, unknown>;
  const { kind: _k, ts: _ts, type: _t, timestamp: _stamp, ...rest } = ev;
  const payload =
    typeof ev.payload === "object" && ev.payload !== null
      ? (ev.payload as SSEEvent["payload"])
      : ({} as SSEEvent["payload"]);
  const out: Record<string, unknown> = {
    ...rest,
    type: eventType(ev),
    payload,
    timestamp: eventTimestamp(ev),
  };
  return out as unknown as SSEEvent;
}

function eventsHavePacing(events: SSEEvent[]): boolean {
  return events.some((ev) => "t_ms" in (ev as object));
}

function stitchRecording(raw: Record<string, unknown>): SSEEvent[] {
  const segments = raw.segments;
  if (!Array.isArray(segments)) return [];
  const out: SSEEvent[] = [];
  for (const segment of segments) {
    if (typeof segment !== "object" || segment === null) continue;
    const events = (segment as { events?: unknown }).events;
    if (!Array.isArray(events)) continue;
    for (const ev of events) {
      out.push(normalizeReplayEvent(ev));
    }
  }
  return out;
}

/**
 * 读入超集文档（向量 / 磁带 / 录制 / 裸 events）。
 * 伴生纯 UI 场景面（白板/首启/手册…）不走此路径。
 */
export function openEventDocument(raw: unknown): EventDocument {
  if (typeof raw !== "object" || raw === null) {
    throw new Error("event document must be an object");
  }
  const doc = raw as Record<string, unknown>;

  // Recorder shape: ``kind: demo_tape_recording`` + ``segments[]``, no top-level events.
  if (
    doc.kind === "demo_tape_recording" ||
    (Array.isArray(doc.segments) && !Array.isArray(doc.events))
  ) {
    const events = stitchRecording(doc);
    return {
      kind: "recording",
      events,
      name: typeof doc.name === "string" ? doc.name : undefined,
      description:
        typeof doc.description === "string" ? doc.description : undefined,
      hasPacing: eventsHavePacing(events),
    };
  }

  if (!Array.isArray(doc.events)) {
    throw new Error(
      "event document requires events[] (or recording segments[])",
    );
  }
  const events = doc.events.map(normalizeReplayEvent);
  const hasPacing = eventsHavePacing(events);
  const name = typeof doc.name === "string" ? doc.name : undefined;
  const description =
    typeof doc.description === "string"
      ? doc.description
      : typeof (doc.meta as { title?: unknown } | undefined)?.title === "string"
        ? String((doc.meta as { title: string }).title)
        : undefined;

  if (isTurnFixture(raw) || (name !== undefined && !("version" in doc))) {
    return { kind: "turn_fixture", events, name, description, hasPacing };
  }
  if ("version" in doc || (typeof doc.meta === "object" && doc.meta !== null)) {
    return {
      kind: "tape",
      events,
      name: name ?? description,
      description,
      hasPacing,
    };
  }
  return { kind: "bare_events", events, name, description, hasPacing };
}

function brandFold(
  source: Omit<FoldReplaySource, typeof foldBrand>,
): FoldReplaySource {
  return source as FoldReplaySource;
}

/**
 * 准备 A 路（FOLD）源：永不 remint。传入已是 `FoldReplaySource` 则原样返回。
 */
export function prepareFoldSource(raw: unknown): FoldReplaySource {
  if (isFoldReplaySource(raw)) return raw;
  const doc = openEventDocument(raw);
  return brandFold({
    consumer: "fold",
    events: doc.events,
    name: doc.name,
    documentKind: doc.kind,
    hasPacing: doc.hasPacing,
  });
}

export function isFoldReplaySource(raw: unknown): raw is FoldReplaySource {
  return (
    typeof raw === "object" &&
    raw !== null &&
    (raw as { consumer?: unknown }).consumer === "fold" &&
    Array.isArray((raw as { events?: unknown }).events)
  );
}

/** 运行时门闩：拒绝非 FOLD 源（防误把 SINK 准备结果直灌）。 */
export function assertFoldSource(source: {
  consumer: ConsumerKind;
}): asserts source is FoldReplaySource {
  if (source.consumer !== "fold") {
    throw new Error(
      `A/B mutual exclusion: cannot fold-inject consumer=${source.consumer} (FOLD and SINK must not dual-inject the same session)`,
    );
  }
}

/** 从已打开文档或事件数组得到 fold 可播事件（兼容旧 `SSEEvent[]` 调用点）。 */
export function foldEventsFrom(
  input: FoldReplaySource | SSEEvent[] | unknown,
): SSEEvent[] {
  if (Array.isArray(input)) {
    return prepareFoldSource({ events: input }).events;
  }
  if (isFoldReplaySource(input)) {
    assertFoldSource(input);
    return input.events;
  }
  return prepareFoldSource(input).events;
}
