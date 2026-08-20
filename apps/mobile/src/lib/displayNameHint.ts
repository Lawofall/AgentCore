const KEY = "agentcore.mobile.displayNameHintDismissed";

function loadDismissed(): string[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((id): id is string => typeof id === "string");
  } catch {
    return [];
  }
}

export function isDisplayNameHintDismissed(userId: string): boolean {
  return loadDismissed().includes(userId);
}

export function dismissDisplayNameHint(userId: string): void {
  const next = new Set(loadDismissed());
  next.add(userId);
  try {
    localStorage.setItem(KEY, JSON.stringify([...next]));
  } catch {
    /* best-effort */
  }
}

/** Storage key — exported for tests only. */
export const DISPLAY_NAME_HINT_KEY = KEY;
