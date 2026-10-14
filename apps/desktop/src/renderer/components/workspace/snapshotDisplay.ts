/**
 * Cloud snapshot presentation helpers (axis-3: auto backup / kept version / system),
 * consumed by the files-page version list (`changesTimeline.ts`).
 *
 * System labels are written by product paths (turn baseline, handoff, export /
 * merge) — they share the same storage as user pins but should not read as
 * 「留版本」. Backend prune policy mirrors this classification
 * (`workspace/snapshot_kinds.py`: baseline max 5, other system max 10, TTL 30d).
 *
 * Timeline visibility is a separate axis on top of the classification: hidden rows
 * still exist, still count against prune policy, and stay restorable through
 * their own surface — see {@link isHiddenSnapshotLabel}.
 */

export type SnapshotKind = "kept" | "auto" | "system";

/** Exact labels written by desktop export / merge flows. */
const SYSTEM_EXACT_LABELS = new Set([
  "导出",
  "导出到本地",
  "浏览器预览",
  "合回到本机",
]);

function isSystemSnapshotLabel(label: string): boolean {
  if (SYSTEM_EXACT_LABELS.has(label)) return true;
  if (label.startsWith("turn-baseline:")) return true;
  if (label.startsWith("handoff:")) return true;
  if (label.startsWith("seal:")) return true;
  return false;
}

export function classifySnapshotLabel(label: string | null): SnapshotKind {
  if (!label) return "auto";
  if (isSystemSnapshotLabel(label)) return "system";
  return "kept";
}

/**
 * Labels withheld from the files-page version list. Two reasons, one outcome:
 * `turn-baseline:` is the same object the 「改动」 tab already lists per turn with
 * file-level diff, and the export / preview / merge labels are transport
 * byproducts nobody asked to keep. `handoff:` stays visible — 「本机交接」 is a
 * milestone with no equivalent elsewhere.
 */
function isHiddenSnapshotLabel(label: string | null): boolean {
  if (!label) return false;
  if (label.startsWith("turn-baseline:")) return true;
  if (label.startsWith("seal:")) return true;
  return SYSTEM_EXACT_LABELS.has(label);
}

/** Timeline-visible subset — see {@link isHiddenSnapshotLabel}. */
export function visibleSnapshots<T extends { label: string | null }>(
  snaps: readonly T[],
): T[] {
  return snaps.filter((s) => !isHiddenSnapshotLabel(s.label));
}

/** Card title shown on a timeline version entry. */
export function snapshotDisplayTitle(label: string | null): string {
  if (!label) return "自动备份";
  if (label.startsWith("turn-baseline:")) return "回合开始前";
  if (label.startsWith("handoff:")) return "本机交接";
  return label;
}

/** Optional tooltip detail (raw system id) when the title is rewritten. */
export function snapshotDisplayHint(label: string | null): string | null {
  if (!label) return null;
  if (label.startsWith("turn-baseline:") || label.startsWith("handoff:")) {
    return label;
  }
  return null;
}
