/**
 * AI 恋综 / AgentTown 节目模式 — `sim.show.*` SSE 事件。
 *
 * 权威源：后端 EventType + pydantic wire payload → `pnpm gen:types` →
 * `events.generated.ts` / `eventTypes.generated.ts`。
 * 本文件按 `sim.show.` 前缀从生成物派生别名，便于节目壳按事件名索引；
 * 勿改回手列事件名，否则生成物增删事件时这里不会报错。
 */

import type { SSEPayloadMap } from "./events.generated";
import type { SSEEventType } from "./eventTypes.generated";

export type {
  SimShowAffectionShiftPayload,
  SimShowDeparturePayload,
  SimShowEpisodeGatePayload,
  SimShowHeartPickPayload,
  SimShowPairFormedPayload,
  SimShowRevealPayload,
  SimShowZeroVoteAlertPayload,
} from "./events.generated";

/** Wire event names for show-mode simulation overlays. */
export type SimShowEventType = Extract<SSEEventType, `sim.show.${string}`>;

export type SimShowPayloadMap = Pick<SSEPayloadMap, SimShowEventType>;
