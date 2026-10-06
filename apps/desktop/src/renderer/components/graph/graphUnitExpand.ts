/**
 * Which collaboration-graph leaders render their direct reports.
 * Default is every non-debate leader expanded. After the user toggles, only
 * explicitly collapsed leaders stay shut — a leader that appears later stays open.
 */
export function resolveGraphExpandedUnits(opts: {
  defaults: ReadonlySet<string>;
  touched: boolean;
  /** Comma-joined leader ids the user collapsed. */
  collapsedFingerprint: string;
  /** Session collapses for non-persisted hosts; null = nothing collapsed. */
  sessionCollapsed: ReadonlySet<string> | null;
  persist: boolean;
}): Set<string> {
  const { defaults, touched, collapsedFingerprint, sessionCollapsed, persist } =
    opts;
  const collapsed = persist
    ? touched
      ? parseCollapsed(collapsedFingerprint)
      : new Set<string>()
    : (sessionCollapsed ?? new Set<string>());
  const expanded = new Set<string>();
  for (const id of defaults) {
    if (!collapsed.has(id)) expanded.add(id);
  }
  return expanded;
}

function parseCollapsed(fingerprint: string): Set<string> {
  return new Set(
    fingerprint
      .split(",")
      .map((id) => id.trim())
      .filter((id) => id.length > 0 && id !== GRAPH_UNIT_EXPAND_TOUCHED),
  );
}

/** Disclosure / session key suffix marking that the user toggled expand at least once. */
export const GRAPH_UNIT_EXPAND_TOUCHED = "__touched";
