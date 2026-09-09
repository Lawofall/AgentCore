import { hasNativeNotification } from "@/lib/capabilities";

/** 窗口失焦或标签页隐藏时走 OS 通知栏；与应用内 toast 互斥（见 teamActivityNotifications）。 */
export function shouldUseNativeNotification(): boolean {
  if (typeof document === "undefined") return false;
  return document.hidden || !document.hasFocus();
}

export async function showNativeNotification(
  title: string,
  body: string,
  opts?: { conversationId?: string },
): Promise<void> {
  if (!hasNativeNotification() || !shouldUseNativeNotification()) return;
  const api = window.notificationApi;
  if (!api?.show) return;
  try {
    const result = await api.show({
      title,
      body,
      conversationId: opts?.conversationId,
    });
    if (!result.ok) {
      console.warn("[nativeNotification]", result.reason);
    }
  } catch (e) {
    console.warn("[nativeNotification] show failed", e);
  }
}
