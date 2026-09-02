/**
 * 本机绑定却本轮走了云端过桥时的弱状态（非引擎切换器、非恐吓）。
 * 钉在最新助手泡脚注，不占输入区。强制关 sidecar 不展示——勿吓大众。
 */
export const CLOUD_BRIDGE_HINT = "本轮经云端协助完成";

export function shouldShowCloudBridgeHint(input: {
  via: "sidecar" | "cloud_bridge" | null;
  sidecarPreference: string;
  isLatestAssistant: boolean;
  isStreaming?: boolean;
}): boolean {
  return (
    input.via === "cloud_bridge" &&
    input.sidecarPreference !== "off" &&
    input.isLatestAssistant &&
    !input.isStreaming
  );
}
