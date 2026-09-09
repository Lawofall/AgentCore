import { describeError } from "@/lib/errors";
import { AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { toast } from "sonner";

// Token-colored leading icons (the toast surface itself stays neutral popover —
// see components/ui/Toaster). Recoverable interruptions use muted, not danger red
// (color-tokens 三层 A). Config remedy already uses the blue info icon below.
const errorIcon = <AlertTriangle size={16} className="text-muted-foreground" />;
const successIcon = <CheckCircle2 size={16} className="text-success" />;
const warningIcon = (
  <AlertTriangle size={16} className="text-muted-foreground" />
);
const infoIcon = <Info size={16} className="text-primary" />;

/**
 * Surface any caught error as a toast, phrased by the shared error map
 * (lib/errors) so a REST failure reads the same as an SSE-turn failure. A no-op
 * for errors handled elsewhere (auth → the login redirect), so callers can pass
 * anything they caught without pre-filtering.
 *
 * - Pass a plain string to show a client-side message verbatim (e.g. input
 *   validation that never hit the server).
 * - Pass `context` to title the toast (e.g. "重命名失败") with the resolved detail
 *   as the description; omit it to show the detail as the title. Holds for a
 *   verbatim string too — a titled call must not lose either half.
 * - An error whose code maps to a remedy (e.g. a missing key → 去配置) gets a
 *   one-click action button that navigates there.
 */
export function notifyError(
  err: unknown,
  context?: string,
  opts?: { action?: { label: string; onClick: () => void } },
): void {
  if (typeof err === "string") {
    toast.error(context ?? err, {
      description: context ? err : undefined,
      icon: errorIcon,
      action: opts?.action,
    });
    return;
  }
  const described = describeError(err);
  if (!described) return; // auth etc. — the redirect already handles it
  const action = described.action;
  const toastAction = action
    ? {
        label: action.label,
        // Hash-router navigation from non-component code: setting the hash
        // drives createHashRouter exactly like a <Link> click.
        onClick: () => {
          window.location.hash = action.href;
        },
      }
    : undefined;
  const title = context ?? described.message;
  const description = context ? described.message : undefined;
  // Config remedy (去配置) — a blue info toast with a one-click fix-it action, matching
  // RetryBanner where the 去配置 affordance is the primary (蓝) action (极简中性：行动=蓝).
  if (action) {
    toast(title, {
      description,
      icon: infoIcon,
      action: toastAction,
    });
    return;
  }
  toast.error(title, {
    description,
    icon: errorIcon,
    action: opts?.action,
  });
}

/** A success toast for a completed user action (e.g. a snapshot was created). */
export function notifySuccess(
  message: string,
  opts?: {
    description?: string;
    action?: { label: string; onClick: () => void };
  },
): void {
  toast.success(message, {
    description: opts?.description,
    icon: successIcon,
    action: opts?.action,
  });
}

/**
 * Surface a failed *client-side* action whose own thrown message is the
 * user-facing detail — e.g. local FS / IPC ops (在资源管理器中显示 / 用默认程序打开 /
 * 复制路径) whose zh reason ("没有访问权限" …) would otherwise be swallowed by
 * {@link notifyError}'s backend-oriented {@link describeError} fallback. Title =
 * `context`; description = the caught error's message verbatim (omitted when empty).
 *
 * Use this only for errors raised locally (not via the REST client / SSE turn) —
 * those still go through {@link notifyError} so a backend `code` is phrased and
 * actioned consistently.
 */
export function notifyActionError(context: string, err: unknown): void {
  const detail =
    err instanceof Error ? err.message : typeof err === "string" ? err : "";
  toast.error(context, {
    description: detail || undefined,
    icon: errorIcon,
  });
}

/**
 * A non-blocking warning toast with an optional one-click action.
 *
 * Distinct from {@link notifyError}: the user's primary action SUCCEEDED, this just
 * flags a degraded side-effect they can act on (e.g. a platform-model fallback
 * when the cloud inference token was unavailable). Muted icon on the neutral surface (color-tokens), so it
 * reads as "heads up", not "failed". The action is caller-supplied (not the error map).
 */
export function notifyWarning(
  message: string,
  opts?: {
    description?: string;
    action?: { label: string; onClick: () => void };
  },
): void {
  toast.warning(message, {
    description: opts?.description,
    icon: warningIcon,
    action: opts?.action,
  });
}

/**
 * A neutral informational toast with an optional one-click action and duration.
 *
 * Distinct from success/warning: neither "操作成功" nor "降级警告" — a heads-up the
 * user may act on (e.g. "安装包已下载 → 打开安装包"). Brand-primary icon on the neutral
 * surface (color-tokens). Pass `duration: Infinity` for a sticky notice that stays
 * until dismissed or acted on.
 */
export function notifyInfo(
  message: string,
  opts?: {
    description?: string;
    duration?: number;
    action?: { label: string; onClick: () => void };
  },
): void {
  toast(message, {
    description: opts?.description,
    duration: opts?.duration,
    icon: infoIcon,
    action: opts?.action,
  });
}
