/**
 * Mobile error triage for chat transport refusals. Shared code catalog from
 * `@agentcore/contract-types` so both clients route the same BYOK codes — but the
 * route and its label stay per-platform on purpose: mobile「去配置」→ 模型配置
 * (BYOK + 组合同页), desktop「去服务商」→ `/more/providers`. Same rule ("land on the
 * page where a key can be entered"), different page names; don't unify the href.
 */
import {
  type RecoveryMomentContext,
  withLocalRecoveryMoment,
} from "@/lib/recoveryMoment";
import {
  KEY_CONFIG_ERROR_CODES,
  isUnstartedSendRefusal as matchUnstartedSendRefusal,
} from "@agentcore/contract-types";
import {
  EMPTY_RESPONSE_CHIP_LABELS,
  LLM_EMPTY_RESPONSE_MESSAGE,
  LLM_ERROR_MESSAGE,
  LLM_UNPRODUCTIVE_MESSAGE,
  TURN_INTERRUPTED_EMPTY_MESSAGE,
  degradedFinishChipLabel,
  isEmptyResponseUserSurface,
} from "@agentcore/protocol-fold-kit";

export { isZeroOutputSendRefusalCode } from "@agentcore/contract-types";
export {
  EMPTY_RESPONSE_CHIP_LABELS,
  LLM_EMPTY_RESPONSE_MESSAGE,
  LLM_ERROR_MESSAGE,
  LLM_UNPRODUCTIVE_MESSAGE,
  TURN_INTERRUPTED_EMPTY_MESSAGE,
  degradedFinishChipLabel,
  isEmptyResponseUserSurface,
};

/** One-click remedy that routes the user to fix the cause (not a retry). */
export interface ErrorAction {
  label: string;
  href: string;
}

/** 手机能填 Key 的落地页。桌面对应页是 `/more/providers`，两端 IA 不同，路径不求一致。 */
export const MODEL_CONFIG_PATH = "/more/model";

/**
 * A non-OK SSE channel response that arrived as plain JSON
 * `{ error: { code, message, context } }` (e.g. 402 LLM_KEY_REQUIRED before the
 * stream opens) rather than an event stream.
 *
 * `context` carries the structured fields the copy no longer spells out — notably
 * the absolute recovery / reset moment a 429 refusal is waiting on
 * ({@link withLocalRecoveryMoment}).
 */
export class StreamHttpError extends Error {
  constructor(
    public status: number,
    public code?: string,
    public serverMessage?: string,
    public context?: RecoveryMomentContext,
  ) {
    super(serverMessage ?? `请求失败 (${status})`);
    this.name = "StreamHttpError";
  }
}

/**
 * 冷 resume 被拒，且挂起帧真的不在了——**诚实失效**，不是「已被别人处理」。
 *
 * 「已被别人处理」不再走这条路：服务端把那种幂等成功改成了 200 + EPHEMERAL
 * `resume_settled`，由 SSE 侧按 journal 的事实收卡。留给 404/410 的只剩两种真失效——挂起
 * 超保留期被清理、回合已重新生成或删除——两句话都在 `serverMessage` 里，交给
 * {@link describeStreamHttpError} 原样呈现，别替后端改口。
 *
 * 唯一用途：决定这次拒绝要不要把卡放回可点。帧不在就不该放回——放回只会请用户一点再点、
 * 次次 404。对齐桌面 `services/turns/regenerate.ts` · `isPausedFrameGone`。
 */
/** Preflight 402/429/平台凭据缺失：发送当没发生。须再配「用户消息未落库」。 */
export function isUnstartedSendRefusal(err: StreamHttpError): boolean {
  return matchUnstartedSendRefusal({ code: err.code, status: err.status });
}

export function isPausedFrameGone(err: unknown): err is StreamHttpError {
  if (!(err instanceof StreamHttpError)) return false;
  return err.status === 404 || err.status === 410;
}

/** Map a backend error `code` to the model-config remedy, or null. */
export function errorActionForCode(
  code: string | undefined,
  opts?: {
    credentialSource?: string | null;
    message?: string | null;
  },
): ErrorAction | null {
  if (code === "INFERENCE_TOKEN_EXPIRED") {
    return null;
  }
  if (code === "LLM_KEY_INVALID") {
    const src =
      opts?.credentialSource === "platform" || opts?.credentialSource === "user"
        ? opts.credentialSource
        : opts?.message?.includes("平台模型暂时不可用")
          ? "platform"
          : "user";
    if (src === "platform") {
      return { label: "接入自己的 Key", href: MODEL_CONFIG_PATH };
    }
    return { label: "去配置", href: MODEL_CONFIG_PATH };
  }
  if (
    code !== undefined &&
    (KEY_CONFIG_ERROR_CODES as readonly string[]).includes(code)
  ) {
    return { label: "去配置", href: MODEL_CONFIG_PATH };
  }
  // 平台额度耗尽 (QUOTA_EXCEEDED, 成本配额与计费 §〇·六 F6): 次级出口「接入自己的 Key」
  // (byok 回合不查配额) — 与桌面对齐; 主文案由后端 message 单一源下发。
  if (code === "QUOTA_EXCEEDED") {
    return { label: "接入自己的 Key", href: MODEL_CONFIG_PATH };
  }
  return null;
}

/**
 * zh message + optional「去配置」 for a refused SSE turn.
 *
 * 429 / 配额闸门的时刻由 {@link withLocalRecoveryMoment} 按本机时区补上；服务端只下发不含
 * 时刻的兜底句，拿不到结构化时刻就原样转述它。
 */
export function describeStreamHttpError(err: StreamHttpError): {
  message: string;
  action: ErrorAction | null;
} {
  let message: string;
  if (err.code === "LLM_KEY_REQUIRED") {
    message = err.serverMessage ?? "请先接入自己的 API Key，再发起对话。";
  } else if (err.serverMessage) {
    message = err.serverMessage;
  } else {
    message = `请求失败 (${err.status})`;
  }
  return {
    message: withLocalRecoveryMoment(message, {
      code: err.code,
      context: err.context,
    }),
    // 分流仍读服务端原文：本地时刻文案是渲染结果，不是判据。
    action: errorActionForCode(err.code, { message: err.serverMessage }),
  };
}

/**
 * Draft / empty-chat copy。平台代付、开箱即用——无「先接入模型」门，keyless 亦直接进欢迎态
 * （BYOK 是「更多 → 模型配置」里的可选升级，不在空态拦路）。Pure helper so the empty-state
 * branch stays unit-testable without mounting ChatPage.
 */
export function emptyChatCopy(): {
  title: string;
  subtitle: string;
  action: ErrorAction | null;
} {
  return {
    title: "开始新对话",
    subtitle: "向你的 Agent 团队提问，或交派一个任务。",
    action: null,
  };
}

/**
 * Visible notice for an empty assistant bubble that finished abnormally.
 * Default ON for failure finishes (error / unproductive / degraded / interrupted);
 * cancelled / paused stay silent (user stop / checkpoint pause). Matches desktop
 * resolveAssistantFailureFace for hard-failure faces; paused is not a failure.
 */
export function emptyFailureNotice(
  finishReason: string | null | undefined,
): string | null {
  if (finishReason === "error") return LLM_ERROR_MESSAGE;
  if (finishReason === "unproductive") return LLM_UNPRODUCTIVE_MESSAGE;
  if (finishReason === "degraded") return LLM_EMPTY_RESPONSE_MESSAGE;
  if (finishReason === "interrupted") return TURN_INTERRUPTED_EMPTY_MESSAGE;
  if (finishReason === "max_rounds") return "已达最大轮次 · 提前收尾";
  return null;
}

/**
 * Empty-bubble failure copy: prefer structured `error.message`
 * (live `chrome.errorMessage` / cold `runs.error.message`); else
 * {@link emptyFailureNotice} for `error` / `unproductive` finishes.
 */
export function emptyFailureVisibleNotice(
  finishReason: string | null | undefined,
  errorMessage?: string | null,
): string | null {
  const specific = errorMessage?.trim();
  if (specific) return specific;
  return emptyFailureNotice(finishReason);
}

/**
 * ChatPage live/history gate for the hard-failure red card (对齐桌面 displayError).
 * - Prefer structured `errorMessage` even when content is non-empty (半成品 + 挂掉).
 * - Empty body → {@link emptyFailureNotice} (`error` / `unproductive`); `paused` is silent.
 * - Body + `finishReason=error` + no payload → synthesize (砍顶栏灰标后禁止静默).
 * - `skip` (streaming / live mid-turn) → null.
 */
export function resolveEmptyFailureNotice(opts: {
  content: string | null | undefined;
  finishReason: string | null | undefined;
  errorMessage?: string | null;
  /** When true (streaming / live unfinished), never synthesize a failure line. */
  skip?: boolean;
  /** Dedicated pause/ask card already owns the UI — silent for paused-only. */
  hasDedicatedPauseOrAskUi?: boolean;
}): string | null {
  if (opts.skip) return null;
  const specific = opts.errorMessage?.trim();
  if (specific) return specific;
  if (
    opts.finishReason === "paused" &&
    opts.hasDedicatedPauseOrAskUi &&
    !(opts.content ?? "").trim()
  ) {
    return null;
  }
  if (!(opts.content ?? "").trim()) {
    return emptyFailureNotice(opts.finishReason);
  }
  // Seam: error finish with body but lost error payload — red card, not gray chip.
  if (opts.finishReason === "error") return emptyFailureNotice("error");
  return null;
}

/**
 * Soft abnormal finishes that warrant a bubble-top chip.
 * Hard `error` is red-card only (永不画顶栏「调用失败」灰标).
 * `cancelled` / `interrupted` omitted — partial body / 已停止 is the terminal signal.
 */
export const FINISH_REASON_META: Record<string, { label: string }> = {
  max_rounds: { label: "已达最大轮次 · 提前收尾" },
  degraded: { label: "空响应收尾" },
  unproductive: { label: "无有效进展 · 提前收尾" },
};

/**
 * Footer「收尾原因」等非 chip 入口——含硬失败文案（可留「调用失败」）.
 */
export const FINISH_REASON_LABELS: Record<string, string> = {
  max_rounds: FINISH_REASON_META.max_rounds.label,
  degraded: FINISH_REASON_META.degraded.label,
  unproductive: FINISH_REASON_META.unproductive.label,
  error: "调用失败",
};
