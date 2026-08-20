/**
 * Persist the last successful login identifier (email or username).
 * Password is never stored — OS password managers use autoComplete instead.
 */

import { uiGet, uiSet } from "@/lib/uiStorage";

/** Global uiStorage leaf → `agentcore:remembered-username`. */
export const REMEMBERED_USERNAME_KEY = "remembered-username";

export function loadRememberedUsername(): string {
  const raw = uiGet<unknown>(REMEMBERED_USERNAME_KEY);
  return typeof raw === "string" ? raw.trim() : "";
}

export function saveRememberedUsername(username: string): void {
  const trimmed = username.trim();
  if (!trimmed) {
    uiSet(REMEMBERED_USERNAME_KEY, undefined);
    return;
  }
  uiSet(REMEMBERED_USERNAME_KEY, trimmed);
}
