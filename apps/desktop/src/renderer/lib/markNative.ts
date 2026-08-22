/**
 * Capacitor WebView 启动：标 `__NATIVE__`、换安全存储。仅 main.webapp 引用。
 */
import { capacitorSecureTokenPersistence } from "@/lib/capacitorSecureStorage";
import { setTokenPersistence } from "@/lib/sessionAuth";
import { Capacitor } from "@capacitor/core";

if (Capacitor.isNativePlatform()) {
  window.__NATIVE__ = true;
  window.__NATIVE_PLATFORM__ =
    Capacitor.getPlatform() === "ios" ? "ios" : "android";
  setTokenPersistence(capacitorSecureTokenPersistence);
}
