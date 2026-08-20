import { logEvent } from "@/lib/log";
import { loadLatestWindow } from "@/services/messages";

/** Immediate + delayed catch-up after harvest / detach may still be writing. */
export const BG_REFRESH_DELAYS_MS = [0, 1500, 6000] as const;

/**
 * Soft-reload the latest message window so journal `runs` (and harvest copy)
 * catch up with a background coordination drive.
 *
 * Used after:
 * - `execution_completed` — harvest 终稿 / 合成用户消息
 * - `execution_detached` — captain 已收口、工人仍跑；live 若丢 `run_completed`，
 *   InlineTeamGraph 靠更新后的 `message.runs` + hydrate 终态优先自愈翻绿
 *
 * `execution_completed` 可早于 harvest 落库；同连接内 terminal phase 会挡住
 * attach，故用短延迟重试覆盖收口回合写完窗口（离开再回来仍走 ConversationPage
 * 正常加载）。
 *
 * 抛错不再静默：每次失败写 `conversation.bg_refresh_failed`，三次都没 apply
 * 再写 `conversation.bg_refresh_exhausted`（产品日志，不弹窗——卡住的 running
 * 节点本身就是 UI 信号；toast 帮不了用户解开）。
 */
export function refreshAfterBackgroundExecution(conversationId: string): void {
  const schedule =
    typeof globalThis.setTimeout === "function"
      ? globalThis.setTimeout.bind(globalThis)
      : null;
  const planned = schedule ? [...BG_REFRESH_DELAYS_MS] : [0];
  const maxAttempts = planned.length;
  let pending = maxAttempts;
  let applied = 0;
  let failed = 0;

  const reload = (attempt: number, delayMs: number): void => {
    void loadLatestWindow(conversationId, { softRefresh: true })
      .then((ok) => {
        if (ok) applied += 1;
      })
      .catch((err: unknown) => {
        failed += 1;
        logEvent("warn", "conversation.bg_refresh_failed", {
          conversation_id: conversationId,
          attempt,
          max_attempts: maxAttempts,
          delay_ms: delayMs,
          ...refreshFailureFields(err),
        });
      })
      .finally(() => {
        pending -= 1;
        if (pending === 0 && applied === 0 && failed > 0) {
          logEvent("warn", "conversation.bg_refresh_exhausted", {
            conversation_id: conversationId,
            attempts: maxAttempts,
            failed,
          });
        }
      });
  };

  for (let i = 0; i < planned.length; i++) {
    const delayMs = planned[i] ?? 0;
    const attempt = i + 1;
    if (delayMs <= 0) {
      reload(attempt, delayMs);
    } else if (schedule) {
      schedule(() => reload(attempt, delayMs), delayMs);
    }
  }
}

function refreshFailureFields(err: unknown): Record<string, unknown> {
  const error_name = err instanceof Error ? err.name : "unknown";
  let http_status: number | null = null;
  let error_code: string | null = null;
  if (err && typeof err === "object") {
    if ("status" in err && typeof err.status === "number") {
      http_status = err.status;
    }
    if ("code" in err && typeof err.code === "string") {
      error_code = err.code;
    }
  }
  return { error_name, http_status, error_code };
}
