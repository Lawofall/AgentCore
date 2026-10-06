export type SwitchboardBulkAction = "open" | "close";

/**
 * Denial-list bulk buttons. All on offers only close; all off offers only
 * open; a mix offers both. Open clears the list (no snapshot of the mix).
 */
export function switchboardBulkActions(
  offCount: number,
  total: number,
): SwitchboardBulkAction[] {
  if (total <= 0) return [];
  if (offCount <= 0) return ["close"];
  if (offCount >= total) return ["open"];
  return ["open", "close"];
}
