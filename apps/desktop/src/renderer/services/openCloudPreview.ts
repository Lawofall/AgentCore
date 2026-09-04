import { api } from "@/services/api";
import { isSafeExternalUrl } from "@shared/safe-url";

/**
 * 云端长驻「打开预览」：用当前登录换票，再在系统浏览器打开返回的 URL。
 *
 * 对标 {@link ./accountToken.ts} 的会话换票，打开面走 BrowserPanel 的
 * `browserApi.openExternal` / `window.open` 降级。票在 URL 里，**绝不**从模型正文
 * 抠地址，也**绝不**把 JWT 写进 UI / toast。
 */

interface PreviewTokenResponse {
  url: string;
}

export async function openCloudPreview(args: {
  conversationId: string;
  processId: string;
  port?: number;
}): Promise<void> {
  const body: {
    conversation_id: string;
    process_id: string;
    port?: number;
  } = {
    conversation_id: args.conversationId,
    process_id: args.processId,
  };
  if (typeof args.port === "number" && Number.isInteger(args.port)) {
    body.port = args.port;
  }

  const res = await api.post<PreviewTokenResponse>("/v1/preview/token", body);
  const url = typeof res?.url === "string" ? res.url.trim() : "";
  // 失败文案不带 URL：query 里是短时票。
  if (!url || !isSafeExternalUrl(url)) {
    throw new Error("预览地址无效");
  }

  const browserApi =
    typeof window !== "undefined" ? window.browserApi : undefined;
  if (browserApi?.openExternal) {
    const r = await browserApi.openExternal({ url });
    if (!r.ok) throw new Error(r.reason || "无法在系统浏览器打开");
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}
