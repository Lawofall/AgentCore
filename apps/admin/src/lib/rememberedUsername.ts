/**
 * Persist the last successful login identifier (email or username).
 * Password is never stored.
 */

/** localStorage key — admin-scoped so it never collides with the desktop client. */
export const REMEMBERED_USERNAME_KEY = "agentcore:admin:remembered-username";

export function loadRememberedUsername(): string {
  try {
    const raw = localStorage.getItem(REMEMBERED_USERNAME_KEY);
    return typeof raw === "string" ? raw.trim() : "";
  } catch {
    return "";
  }
}

export function saveRememberedUsername(username: string): void {
  try {
    const trimmed = username.trim();
    if (!trimmed) {
      localStorage.removeItem(REMEMBERED_USERNAME_KEY);
      return;
    }
    localStorage.setItem(REMEMBERED_USERNAME_KEY, trimmed);
  } catch {
    /* private mode / quota — ignore */
  }
}
