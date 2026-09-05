import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Spinner";
import {
  loginIdentifierError,
  passwordFieldError,
} from "@/lib/emailAuth";
import {
  loadRememberedUsername,
  saveRememberedUsername,
} from "@/lib/rememberedUsername";
import { errorMessage } from "@/services/api";
import { login, loginMfa } from "@/services/auth";
import { useAuthStore } from "@/stores/auth";
import { ArrowLeft, Eye, EyeOff, ShieldCheck } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";

type Step = "credentials" | "mfa";
/** Second factor: the authenticator's rotating code, or one single-use recovery code. */
type MfaMode = "totp" | "recovery";

const TOTP_LENGTH = 6;
/** `secrets.token_hex(8)` on the backend — 16 hex chars, dashes are cosmetic. */
const RECOVERY_LENGTH = 16;

export function LoginPage() {
  const setAuthenticated = useAuthStore((s) => s.setAuthenticated);
  const setForbidden = useAuthStore((s) => s.setForbidden);
  const pendingMfaToken = useAuthStore((s) => s.pendingMfaToken);
  const setPendingMfaToken = useAuthStore((s) => s.setPendingMfaToken);

  const [step, setStep] = useState<Step>(pendingMfaToken ? "mfa" : "credentials");
  const [username, setUsername] = useState(() => loadRememberedUsername());
  const [password, setPassword] = useState("");
  const [revealPassword, setRevealPassword] = useState(false);
  const [attempted, setAttempted] = useState(false);
  /** Default off — session persistence is opt-in via「保持登录」. */
  const [persistSession, setPersistSession] = useState(false);
  const [totpCode, setTotpCode] = useState("");
  const [mfaMode, setMfaMode] = useState<MfaMode>("totp");
  const [submitting, setSubmitting] = useState(false);

  const mfaLength = mfaMode === "totp" ? TOTP_LENGTH : RECOVERY_LENGTH;
  const mfaReady = totpCode.length === mfaLength;

  const identifierErr = loginIdentifierError(username);
  const passwordErr = passwordFieldError(password);
  const identifierDirty = attempted || username.length > 0;
  const passwordDirty = attempted || password.length > 0;
  const credentialsReady = !identifierErr && !passwordErr;

  const handleCredentials = async (e: FormEvent) => {
    e.preventDefault();
    setAttempted(true);
    if (!credentialsReady || submitting) return;
    setSubmitting(true);
    try {
      const trimmed = username.trim();
      const outcome = await login(trimmed, password, persistSession);
      saveRememberedUsername(trimmed);
      if (outcome.kind === "mfa_required") {
        setPendingMfaToken(outcome.pendingToken);
        setStep("mfa");
        setSubmitting(false);
        return;
      }
      if (outcome.kind === "mfa_setup_required") {
        if (outcome.user.role === "admin") {
          setAuthenticated(outcome.user, { mfaSetupRequired: true });
        } else {
          setForbidden(outcome.user);
        }
        return;
      }
      if (outcome.user.role === "admin") setAuthenticated(outcome.user);
      else setForbidden(outcome.user);
    } catch (err) {
      toast.error(errorMessage(err));
      setSubmitting(false);
    }
  };

  const handleMfa = async (e: FormEvent) => {
    e.preventDefault();
    const token = pendingMfaToken;
    if (!token || !mfaReady || submitting) return;
    setSubmitting(true);
    try {
      const entered = totpCode.trim();
      const outcome = await loginMfa(
        token,
        mfaMode === "totp" ? { code: entered } : { recoveryCode: entered },
      );
      if (outcome.kind !== "success") {
        toast.error("验证失败，请重试");
        setSubmitting(false);
        return;
      }
      saveRememberedUsername(username.trim());
      if (outcome.user.role === "admin") setAuthenticated(outcome.user);
      else setForbidden(outcome.user);
    } catch (err) {
      toast.error(errorMessage(err));
      setSubmitting(false);
    }
  };

  const handleBack = () => {
    setPendingMfaToken(null);
    setStep("credentials");
    setTotpCode("");
    setMfaMode("totp");
    setSubmitting(false);
  };

  const switchMfaMode = () => {
    setMfaMode((m) => (m === "totp" ? "recovery" : "totp"));
    setTotpCode("");
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-xs">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <div className="flex size-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <ShieldCheck size={24} />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-foreground">
              AgentCore 管理后台
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {step !== "mfa"
                ? "仅限平台管理员登录"
                : mfaMode === "totp"
                  ? "输入身份验证器中的 6 位验证码"
                  : "输入绑定时保存的恢复码，每个仅可使用一次"}
            </p>
          </div>
        </div>

        {step === "credentials" ? (
          <form
            noValidate
            onSubmit={(e) => void handleCredentials(e)}
            className="flex flex-col gap-3"
          >
            <div className="flex flex-col gap-1">
              <Input
                type="text"
                autoComplete="username"
                placeholder="邮箱或用户名"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={submitting}
                aria-invalid={
                  Boolean(identifierDirty && identifierErr) || undefined
                }
                // biome-ignore lint/a11y/noAutofocus: single-purpose login form
                autoFocus
              />
              {identifierDirty && identifierErr && (
                <p className="text-xs text-muted-foreground" role="alert">
                  {identifierErr}
                </p>
              )}
            </div>
            <div className="flex flex-col gap-1">
              <div className="relative">
                <Input
                  type={revealPassword ? "text" : "password"}
                  autoComplete="current-password"
                  placeholder="密码"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={submitting}
                  aria-invalid={
                    Boolean(passwordDirty && passwordErr) || undefined
                  }
                  className="pr-10"
                />
                <button
                  type="button"
                  aria-label={revealPassword ? "隐藏密码" : "显示密码"}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  onClick={() => setRevealPassword((v) => !v)}
                >
                  {revealPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              {passwordDirty && passwordErr && (
                <p className="text-xs text-muted-foreground" role="alert">
                  {passwordErr}
                </p>
              )}
            </div>
            <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
              <input
                type="checkbox"
                className="size-4 rounded border-input accent-primary"
                checked={persistSession}
                onChange={(e) => setPersistSession(e.target.checked)}
                disabled={submitting}
              />
              保持登录
            </label>
            <Button
              type="submit"
              disabled={submitting}
              className="mt-1 w-full"
            >
              {submitting && <Spinner />}
              {submitting ? "登录中…" : "登录"}
            </Button>
          </form>
        ) : (
          <form onSubmit={(e) => void handleMfa(e)} className="flex flex-col gap-3">
            {mfaMode === "totp" ? (
              <Input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="验证码（6 位）"
                aria-label="身份验证器验证码"
                value={totpCode}
                onChange={(e) =>
                  setTotpCode(e.target.value.replace(/\D/g, "").slice(0, TOTP_LENGTH))
                }
                disabled={submitting}
                autoFocus
              />
            ) : (
              <Input
                type="text"
                inputMode="text"
                autoComplete="one-time-code"
                placeholder="恢复码（16 位）"
                aria-label="恢复码"
                className="font-mono"
                value={totpCode}
                onChange={(e) =>
                  // Dashes/spaces are how people write these down; the backend
                  // normalizes the same way before matching.
                  setTotpCode(
                    e.target.value
                      .replace(/[^0-9a-fA-F]/g, "")
                      .toLowerCase()
                      .slice(0, RECOVERY_LENGTH),
                  )
                }
                disabled={submitting}
                autoFocus
              />
            )}
            <Button
              type="submit"
              disabled={submitting || !mfaReady}
              className="mt-1 w-full"
            >
              {submitting && <Spinner />}
              {submitting ? "验证中…" : "验证并登录"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={switchMfaMode}
              disabled={submitting}
              className="w-full"
            >
              {mfaMode === "totp" ? "无法使用验证器？用恢复码登录" : "改用验证器验证码"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleBack}
              disabled={submitting}
              className="w-full"
            >
              <ArrowLeft size={14} />
              返回重新输入密码
            </Button>
          </form>
        )}

        {step === "credentials" && (
          <p className="mt-4 text-center text-xs text-muted-foreground">
            忘记密码？请联系其他平台管理员在用户管理中重置，或联系运维。
          </p>
        )}
      </div>
    </div>
  );
}
