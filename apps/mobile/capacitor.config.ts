import type { CapacitorConfig } from "@capacitor/cli";

// Capacitor 壳（前端技术与架构 §五）。产品页是桌面 dist-web，不是本目录 SPA。
// `appId` 是 iOS/Android 共用的 reverse-domain；上架后改会让 Keychain/Keystore（含 Bearer）失效。
const config: CapacitorConfig = {
  appId: "com.agentcore.mobile",
  appName: "AgentCore",
  // 产品页是桌面 renderer 的 dist-web（`pnpm build` → prepare-cap-web）。
  webDir: "../desktop/dist-web",
  // WebView + DecorView fill under transparent system bars (Cap 8 edge-to-edge).
  // Keep in sync with android `shellBackground` / mobile-light `--panel`.
  backgroundColor: "#ffffff",
  plugins: {
    // Dark icons on the light shell (Cap LIGHT = light appearance / dark glyphs; not the legacy StatusBar plugin).
    SystemBars: {
      style: "LIGHT",
    },
    // iOS-only knob: without it iOS suppresses the「需要你」pause alert while the app is
    // foregrounded. Android ignores presentationOptions entirely — a foreground message just
    // fires the JS `pushNotificationReceived` event and posts no tray notification. That gap is
    // covered on purpose by the in-app AiAttentionBanner (firehose `ai_attention`), so don't
    // "fix" it by posting a local notification from JS.
    PushNotifications: {
      presentationOptions: ["badge", "sound", "alert"],
    },
  },
};

export default config;
