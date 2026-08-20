// Last-used login identifier, email or username (prefs only — never password).
// next visit to LoginPage can prefill. localStorage works for both Capacitor WebView
// and plain web; mirrors agentcore.mobile.* prefs (e.g. lastModelProfile).

const KEY = "agentcore.mobile.rememberedUsername";

export function getRememberedUsername(): string | null {
  try {
    const raw = localStorage.getItem(KEY)?.trim();
    return raw || null;
  } catch {
    return null;
  }
}

export function setRememberedUsername(username: string): void {
  const trimmed = username.trim();
  if (!trimmed) return;
  try {
    localStorage.setItem(KEY, trimmed);
  } catch {
    /* best-effort */
  }
}

export function clearRememberedUsername(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* best-effort */
  }
}

/** Storage key — exported for tests only. */
export const REMEMBERED_USERNAME_KEY = KEY;
