import type { ExecutionStatus, TeamNote } from "@/stores/execution";

/**
 * Canvas / chat shared rule for whether the team-notes wall starts open.
 * Running turns with at least one `active` note, or a raised empty wall, expand
 * by default; finished / stopped turns stay collapsed to a「便签 N」signal.
 */
export function teamNotesDefaultExpanded(
  status: ExecutionStatus | null | undefined,
  notes: readonly TeamNote[],
  noteWall = false,
): boolean {
  if (status !== "running") return false;
  if (notes.some((n) => n.status === "active")) return true;
  return noteWall && notes.length === 0;
}
