import { uiGet, uiSet } from "@/lib/uiStorage";

/** Global uiStorage leaf → `agentcore:display-name-hint-dismissed`. */
export const DISPLAY_NAME_HINT_KEY = "display-name-hint-dismissed";

function loadDismissed(): string[] {
  const raw = uiGet<unknown>(DISPLAY_NAME_HINT_KEY);
  if (!Array.isArray(raw)) return [];
  return raw.filter((id): id is string => typeof id === "string");
}

export function isDisplayNameHintDismissed(userId: string): boolean {
  return loadDismissed().includes(userId);
}

export function dismissDisplayNameHint(userId: string): void {
  const next = new Set(loadDismissed());
  next.add(userId);
  uiSet(DISPLAY_NAME_HINT_KEY, [...next]);
}
