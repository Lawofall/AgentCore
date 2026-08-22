import { isNativeRuntime, isWebRuntime } from "@/lib/capabilities";
import { cachedDeviceId } from "@/services/deviceIdentity";

declare const __APP_VERSION__: string;
declare const __APP_GIT_SHA__: string;

/** Electron 外壳为 desktop；浏览器 web 为 web；Capacitor 为 android / ios。 */
export function clientPlatform(): "desktop" | "web" | "android" | "ios" {
  if (isNativeRuntime()) {
    return window.__NATIVE_PLATFORM__ === "ios" ? "ios" : "android";
  }
  return isWebRuntime() ? "web" : "desktop";
}

export function clientVersion(): string {
  return typeof __APP_VERSION__ !== "undefined" ? __APP_VERSION__ : "dev";
}

export function clientGitSha(): string {
  return typeof __APP_GIT_SHA__ !== "undefined" ? __APP_GIT_SHA__ : "unknown";
}

/**
 * Headers every request carries.
 *
 * `X-Client-Device` marks which install started a turn, so the server can run
 * that turn's file / shell / mount ops here rather than on another machine of
 * the same account. Omitted in web runtime and before the device id resolves —
 * this install is then not a fulfiller, or not yet known to be one.
 */
export function clientHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    "X-Client-Platform": clientPlatform(),
    "X-Client-Version": clientVersion(),
  };
  const deviceId = isWebRuntime() ? null : cachedDeviceId();
  if (deviceId) headers["X-Client-Device"] = deviceId;
  return headers;
}

export function formatGitSha(sha: string): string {
  return sha === "unknown" ? "未标记（本地开发）" : sha;
}
