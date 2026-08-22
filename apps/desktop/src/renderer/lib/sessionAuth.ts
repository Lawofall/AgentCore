/**
 * Capacitor / 非同源壳的 Bearer 会话（安全存储 + Authorization）。
 * Electron 与生产 web（同源 cookie）不走这里。权威 → 前端技术 §五。
 */

export interface BearerTokens {
  access_token: string;
  refresh_token: string;
}

export interface TokenPersistence {
  load(): Promise<BearerTokens | null>;
  save(tokens: BearerTokens): Promise<void>;
  clear(): Promise<void>;
}

let persistence: TokenPersistence | null = null;
let cached: BearerTokens | null = null;

export function isBearerAuth(): boolean {
  return typeof window !== "undefined" && window.__NATIVE__ === true;
}

export function setTokenPersistence(next: TokenPersistence): void {
  persistence = next;
}

export async function hydrateBearerTokens(): Promise<BearerTokens | null> {
  if (!persistence) {
    cached = null;
    return null;
  }
  try {
    cached = await persistence.load();
  } catch {
    cached = null;
  }
  return cached;
}

export function getBearerTokens(): BearerTokens | null {
  return cached;
}

export function setBearerTokens(tokens: BearerTokens): void {
  cached = tokens;
  void persistence?.save(tokens).catch(() => {});
}

export function clearBearerTokens(): void {
  cached = null;
  void persistence?.clear().catch(() => {});
}

export function bearerAuthHeader(): Record<string, string> {
  const tokens = cached;
  return tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {};
}

export function sessionCredentials(): RequestCredentials {
  return isBearerAuth() ? "omit" : "include";
}
