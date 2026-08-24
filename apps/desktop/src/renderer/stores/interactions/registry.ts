/**
 * Single registration table for user-facing decision / ask interactions.
 *
 * Wire shape (required/resolved event + id field) and behavior flags come from
 * codegen (`INTERACTION_KIND_WIRE`). Kind groupings (hot / cold-resume / hot-gate
 * / stage) and submit path are derived from those flags — do not
 * hand-copy kind names. This module adds desktop-only metadata: timeline
 * marker, SSE side-effects. Card titles live in protocol-fold-kit
 * (`INTERACTION_CARD_NAME`). Card components / cold resume renderers live
 * in `registryUi.tsx` to keep this file React-free.
 *
 * Adding a new decision card = kit card title + one row here (+ UI binding)
 * instead of parallel switches across types / store maps / SSE / fold /
 * timeline.
 */

import {
  INTERACTION_KIND_WIRE,
  USER_INTERACTION_KIND_VALUES,
  type UserInteractionKind,
} from "@agentcore/contract-types";
import { INTERACTION_CARD_NAME } from "@agentcore/protocol-fold-kit";

export { INTERACTION_CARD_NAME };

export type InteractionKind = UserInteractionKind;

/**
 * Desktop transport for resolving a card. Derived from wire flags:
 * hot → "hot"; pausesTurn && !hot → "cold";
 * reconnectAnswerable && !hot && !pausesTurn → "stage"; otherwise throw
 * (no leftover compose path).
 */
export type InteractionSubmitPath = "cold" | "hot" | "stage";

export function submitPathOf(kind: InteractionKind): InteractionSubmitPath {
  const w = INTERACTION_KIND_WIRE[kind];
  if (w.hot) return "hot";
  if (w.pausesTurn) return "cold";
  if (w.reconnectAnswerable) return "stage";
  throw new Error(`no submit path for interaction kind ${kind}`);
}

function kindsWhere(
  pred: (kind: InteractionKind) => boolean,
): InteractionKind[] {
  return USER_INTERACTION_KIND_VALUES.filter(pred);
}

/** In-process Future kinds (`hot`). */
export const HOT_INTERACTION_KINDS = kindsWhere(
  (kind) => INTERACTION_KIND_WIRE[kind].hot,
);

/** Cold-path kinds that persist to paused_turns (`pausesTurn && !hot`). */
export const COLD_RESUME_KINDS = kindsWhere((kind) => {
  const w = INTERACTION_KIND_WIRE[kind];
  return w.pausesTurn && !w.hot;
});

export type ColdResumeKind = (typeof COLD_RESUME_KINDS)[number];

/** Retired kickoff kinds — wire kind remains; no operable desktop surface. */
export function isRetiredKickoffKind(kind: string): kind is "team_preview" {
  return kind === "team_preview";
}

/** Live turn blocked on the user (`hot && pausesTurn`). Today: approval. */
export const HOT_GATE_INTERACTION_KINDS = kindsWhere((kind) => {
  const w = INTERACTION_KIND_WIRE[kind];
  return w.hot && w.pausesTurn;
});

export type HotGateInteractionKind =
  (typeof HOT_GATE_INTERACTION_KINDS)[number];

/** Cross-turn durable card (`reconnectAnswerable && !hot && !pausesTurn`). */
export const STAGE_INTERACTION_KINDS = kindsWhere((kind) => {
  const w = INTERACTION_KIND_WIRE[kind];
  return w.reconnectAnswerable && !w.hot && !w.pausesTurn;
});

export type StageInteractionKind = (typeof STAGE_INTERACTION_KINDS)[number];

const HOT_KIND_SET = new Set<string>(HOT_INTERACTION_KINDS);
const COLD_RESUME_KIND_SET = new Set<string>(COLD_RESUME_KINDS);
const HOT_GATE_KIND_SET = new Set<string>(HOT_GATE_INTERACTION_KINDS);
const STAGE_KIND_SET = new Set<string>(STAGE_INTERACTION_KINDS);

export function isHotInteractionKind(
  kind: string,
): kind is (typeof HOT_INTERACTION_KINDS)[number] {
  return HOT_KIND_SET.has(kind);
}

export function isColdResumeKind(kind: string): kind is ColdResumeKind {
  return COLD_RESUME_KIND_SET.has(kind);
}

export function isHotGateInteractionKind(
  kind: string,
): kind is HotGateInteractionKind {
  return HOT_GATE_KIND_SET.has(kind);
}

/**
 * Title for a hot-gate chip. Known kinds read {@link INTERACTION_CARD_NAME};
 * unknown keys keep their own id (never inherit another member's title).
 */
export function hotGateKindTitle(kind: string): string {
  if (isHotGateInteractionKind(kind)) return INTERACTION_CARD_NAME[kind];
  return kind;
}

export function isStageInteractionKind(
  kind: string,
): kind is StageInteractionKind {
  return STAGE_KIND_SET.has(kind);
}

/** Process-step discriminant stamped into the CEO message lane. */
export type TimelineProcessKind =
  | "checkpoint"
  | "plan_review"
  | "team_preview"
  | "escalation"
  | "approval"
  | "stage_card";

export interface TimelineMarkerDef {
  processKind: TimelineProcessKind;
  /** Id field on the ProcessStep wire shape. */
  stepIdField:
    | "checkpoint_id"
    | "escalation_id"
    | "approval_id"
    | "stage_card_id";
  /** Historical marker order (before final `team`); not the current kickoff card. */
  insertBeforeTeam?: boolean;
}

export interface InteractionSseRequiredEffects {
  flushBuffers?: boolean;
  recordExecFrame?: boolean;
}

export interface InteractionSseResolvedEffects {
  removePausedTurn?: boolean;
  flushFrames?: boolean;
  recordExecFrame?: boolean;
}

/**
 * Where the live SSE pair is dispatched. Escalation frames ride the execution
 * handler (team projection); everything else uses the interaction handler.
 */
export type InteractionSseVia = "interaction" | "execution";

export interface InteractionKindDef {
  kind: InteractionKind;
  timeline?: TimelineMarkerDef;
  sseVia?: InteractionSseVia;
  sseRequired?: InteractionSseRequiredEffects;
  sseResolved?: InteractionSseResolvedEffects;
}

/** The registry — one row per UserInteractionKind. */
export const INTERACTION_REGISTRY: readonly InteractionKindDef[] = [
  {
    kind: "approval",
    timeline: {
      processKind: "approval",
      stepIdField: "approval_id",
    },
    // Flush rAF-buffered CEO prose BEFORE stamping so the 痕迹 marker lands after
    // the same-round lead-in (mirrors the golden's [content, approval] order).
    sseRequired: { flushBuffers: true },
  },
  {
    kind: "escalation",
    sseVia: "execution",
    timeline: {
      processKind: "escalation",
      stepIdField: "escalation_id",
    },
  },
  {
    kind: "ask_user",
    timeline: {
      processKind: "checkpoint",
      stepIdField: "checkpoint_id",
    },
    sseRequired: { flushBuffers: true },
    sseResolved: { removePausedTurn: true },
  },
  {
    kind: "plan_review",
    timeline: {
      processKind: "plan_review",
      stepIdField: "checkpoint_id",
    },
    sseRequired: { flushBuffers: true, recordExecFrame: true },
    sseResolved: {
      removePausedTurn: true,
      flushFrames: true,
      recordExecFrame: true,
    },
  },
  {
    kind: "team_preview",
    timeline: {
      processKind: "team_preview",
      stepIdField: "checkpoint_id",
      insertBeforeTeam: true,
    },
    sseRequired: { flushBuffers: true },
    sseResolved: { removePausedTurn: true },
  },
  {
    kind: "stage_card",
    // 跨回合耐久卡：resolve 起新回合 SSE（非 cold resume / 非 hot Future）。
    timeline: {
      processKind: "stage_card",
      stepIdField: "stage_card_id",
    },
    sseRequired: { flushBuffers: true },
  },
];

// ── Derived indexes (no parallel hand maps) ─────────────────────────────

function buildByKind(): Record<InteractionKind, InteractionKindDef> {
  const out = {} as Record<InteractionKind, InteractionKindDef>;
  for (const def of INTERACTION_REGISTRY) {
    out[def.kind] = def;
  }
  return out;
}

export const INTERACTION_BY_KIND: Record<InteractionKind, InteractionKindDef> =
  buildByKind();

export const INTERACTION_SUBMIT_PATH: Record<
  InteractionKind,
  InteractionSubmitPath
> = Object.fromEntries(
  USER_INTERACTION_KIND_VALUES.map((kind) => [kind, submitPathOf(kind)]),
) as Record<InteractionKind, InteractionSubmitPath>;

export const INTERACTION_ID_FIELD: Record<InteractionKind, string> =
  Object.fromEntries(
    (
      Object.entries(INTERACTION_KIND_WIRE) as Array<
        [InteractionKind, { idField: string }]
      >
    ).map(([kind, wire]) => [kind, wire.idField]),
  ) as Record<InteractionKind, string>;

const REQUIRED_EVENT_TO_KIND = new Map<string, InteractionKind>();
const RESOLVED_EVENT_TO_KIND = new Map<string, InteractionKind>();
const TIMELINE_BY_PROCESS = new Map<TimelineProcessKind, InteractionKindDef>();

for (const def of INTERACTION_REGISTRY) {
  const wire = INTERACTION_KIND_WIRE[def.kind];
  REQUIRED_EVENT_TO_KIND.set(wire.requiredEvent, def.kind);
  if (wire.resolvedEvent) {
    RESOLVED_EVENT_TO_KIND.set(wire.resolvedEvent, def.kind);
  }
  if (def.timeline) {
    TIMELINE_BY_PROCESS.set(def.timeline.processKind, def);
  }
}

export function kindFromRequiredEvent(
  eventType: string,
): InteractionKind | null {
  return REQUIRED_EVENT_TO_KIND.get(eventType) ?? null;
}

export function kindFromResolvedEvent(
  eventType: string,
): InteractionKind | null {
  return RESOLVED_EVENT_TO_KIND.get(eventType) ?? null;
}

export function defFromRequiredEvent(
  eventType: string,
): InteractionKindDef | null {
  const kind = kindFromRequiredEvent(eventType);
  return kind ? INTERACTION_BY_KIND[kind] : null;
}

export function defFromResolvedEvent(
  eventType: string,
): InteractionKindDef | null {
  const kind = kindFromResolvedEvent(eventType);
  return kind ? INTERACTION_BY_KIND[kind] : null;
}

export function defFromTimelineProcess(
  processKind: TimelineProcessKind,
): InteractionKindDef | null {
  return TIMELINE_BY_PROCESS.get(processKind) ?? null;
}

export function wireFor(kind: InteractionKind) {
  return INTERACTION_KIND_WIRE[kind];
}

/** Interaction-channel SSE event types (excludes escalation → execution handler). */
export function interactionChannelEventTypes(): ReadonlySet<string> {
  const out = new Set<string>();
  for (const def of INTERACTION_REGISTRY) {
    if ((def.sseVia ?? "interaction") !== "interaction") continue;
    const wire = INTERACTION_KIND_WIRE[def.kind];
    out.add(wire.requiredEvent);
    if (wire.resolvedEvent) out.add(wire.resolvedEvent);
  }
  return out;
}
