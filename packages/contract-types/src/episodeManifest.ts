/**
 * EpisodeManifest v1 — AgentTown 节目模式导播产物（自包含台词 / 镜头 / tick 引用）。
 *
 * 手写 TS 真源。播出消费镜像 `apps/town/Assets/Scripts/Show/EpisodeManifest.cs`；
 * 后端 pydantic 镜像 `apps/server/agentcore/simulation/show/manifest.py`。
 * 官网无观看层副本。世界运动不内嵌：shot 按 `tick_at` 引用 run tick 快照。
 */

export const EPISODE_MANIFEST_VERSION = 1 as const;

export type EpisodeSegmentKind =
  | "recap"
  | "day"
  | "night"
  | "ceremony"
  | "quiz"
  | "reveal"
  | "epilogue";

export type EpisodeCameraKind =
  | "wide_establish"
  | "follow_pair"
  | "orbit_group"
  | "push_in"
  | "reveal_closeup";

/** Inclusive tick window on the source simulation run. */
export interface EpisodeTickSpan {
  start: number;
  end: number;
}

/**
 * Overlay 字幕 / 独白 / 字卡 — 文本自包含，锚在 tick 或 shot。
 * kind 供 AgentTown HUD 播出（`EpisodeOverlayView`），不是官网 demo 事件。
 */
export type EpisodeOverlay =
  | {
      kind: "title_card";
      id?: string;
      text: string;
      sub?: string;
      tick_at?: number;
      shot_id?: string;
    }
  | {
      kind: "narration";
      id?: string;
      text: string;
      tick_at?: number;
      shot_id?: string;
    }
  | {
      kind: "line";
      id?: string;
      who: string;
      text: string;
      tick_at?: number;
      shot_id?: string;
    }
  | {
      kind: "monologue";
      id?: string;
      who: string;
      text: string;
      tick_at?: number;
      shot_id?: string;
    }
  | {
      kind: "action";
      id?: string;
      who?: string;
      text: string;
      tick_at?: number;
      shot_id?: string;
    }
  | {
      kind: "scene";
      id?: string;
      title: string;
      time?: string;
      present: string[];
      mood?: "day" | "fire" | "night";
      tick_at?: number;
      shot_id?: string;
    }
  | {
      kind: "relation";
      id?: string;
      hints: EpisodeRelationHint[];
      tick_at?: number;
      shot_id?: string;
    };

export type EpisodeRelationKind =
  | "spark"
  | "oneway"
  | "tension"
  | "cooling"
  | "chaos"
  | "unknown";

export interface EpisodeRelationHint {
  from: string;
  to: string | "all";
  kind: EpisodeRelationKind;
  label: string;
}

/** One camera beat; world pose comes from run tick snapshot at `tick_at`. */
export interface EpisodeShot {
  id: string;
  camera: EpisodeCameraKind;
  /** Agent ids (or empty for establish / empty room). */
  subjects: string[];
  tick_at: number;
  /** Optional playback hint; omit → client default. */
  duration_hint_ms?: number;
}

export interface EpisodeSegment {
  id: string;
  kind: EpisodeSegmentKind;
  /** HUD / progress label, e.g.「白天 · 市集」. */
  label?: string;
  tick_span: EpisodeTickSpan;
  shots: EpisodeShot[];
  /** Self-contained captions anchored to tick or shot. */
  overlays?: EpisodeOverlay[];
}

export interface EpisodeQuiz {
  focus: string;
  question: string;
  hint?: string;
  options: string[];
  answer: string;
  /** Where the quiz inserts in the playback timeline. */
  insert_at: {
    tick?: number;
    after_segment_id?: string;
    shot_id?: string;
  };
}

export interface EpisodeRevealStep {
  who: string;
  pick: string;
  note?: string;
}

export interface EpisodeReveal {
  intro?: string;
  steps: EpisodeRevealStep[];
  outro?: string[];
  /** Optional overlay id (e.g. answer monologue) for quiz settlement replay. */
  answer_overlay_id?: string;
}

export interface EpisodeHighlight {
  id: string;
  title: string;
  quote: string;
  by: string;
  /** Jump target: prefer shot; overlay id as secondary. */
  shot_id?: string;
  overlay_id?: string;
}

export interface EpisodeNextTeaser {
  title: string;
  hook: string;
}

export interface EpisodeManifest {
  version: typeof EPISODE_MANIFEST_VERSION;
  season: string;
  episode_no: number;
  title: string;
  /** Source simulation run; world motion via tick snapshots. */
  run_id: string;
  tick_range: EpisodeTickSpan;
  tagline?: string;
  rule_line?: string;
  segments: EpisodeSegment[];
  quiz?: EpisodeQuiz;
  reveal?: EpisodeReveal;
  /** Exactly three highlights in product v1; typed as array for forward compat. */
  highlights: EpisodeHighlight[];
  next_teaser: EpisodeNextTeaser;
}
