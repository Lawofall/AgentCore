import type { BearerTokens, TokenPersistence } from "@/lib/sessionAuth";
import { SecureStorage } from "@aparajita/capacitor-secure-storage";

const KEY = "tokens";

function isTokens(value: unknown): value is BearerTokens {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as BearerTokens).access_token === "string" &&
    typeof (value as BearerTokens).refresh_token === "string"
  );
}

/** OS Keychain / Keystore。只从 markNative 注入，Electron / 浏览器不 import 本文件。 */
export const capacitorSecureTokenPersistence: TokenPersistence = {
  async load() {
    try {
      const value = await SecureStorage.get(KEY);
      return isTokens(value)
        ? {
            access_token: value.access_token,
            refresh_token: value.refresh_token,
          }
        : null;
    } catch {
      return null;
    }
  },
  async save(tokens) {
    await SecureStorage.set(KEY, {
      access_token: tokens.access_token,
      refresh_token: tokens.refresh_token,
    });
  },
  async clear() {
    await SecureStorage.remove(KEY);
  },
};
