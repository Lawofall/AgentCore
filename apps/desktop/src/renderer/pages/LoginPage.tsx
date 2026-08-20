import { BrandMark } from "@/components/brand/BrandMark";
import { Button } from "@/components/ui";
import {
  MIN_PASSWORD_LENGTH,
  emailCodeError,
  emailFieldError,
  formatEmailCodeValidityHint,
  isLikelyEmail,
  loginIdentifierError,
  normalizeEmailCode,
  passwordFieldError,
  useResendCountdown,
} from "@/lib/emailAuth";
import {
  loadRememberedUsername,
  saveRememberedUsername,
} from "@/lib/rememberedUsername";
import { LegalDocPane } from "@/pages/legal/LegalDocPane";
import type { LegalDocId } from "@/pages/legal/types";
import {
  AuthOutcome,
  FieldError,
  PasswordField,
  checkClass,
  inputClass,
} from "@/pages/login/AuthBits";
import { persistAgentTownSession } from "@/services/agentTownSession";
import { ApiError } from "@/services/api";
import {
  forgotPassword,
  login,
  resetPassword,
  sendRegisterCode,
  verifyRegister,
} from "@/services/auth";
import { cacheShellMeta } from "@/services/offlineCache";
import { useAuthStore } from "@/stores/auth";
import { useRef, useState } from "react";

type Mode = "login" | "register" | "forgot";
type ForgotStep = "email" | "reset";

/** Pull a human-readable message out of the API's `{error:{message}}` body. */
function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.code === "ADMIN_PRODUCT_FORBIDDEN") {
      return "此账号为管理员账号，请使用管理后台登录";
    }
    if (err.code === "EMAIL_NOT_VERIFIED") {
      return "请先验证邮箱";
    }
    try {
      const parsed = JSON.parse(err.body);
      const msg = parsed?.error?.message ?? parsed?.detail;
      if (typeof msg === "string" && msg) return msg;
    } catch {
      /* non-JSON body */
    }
    if (err.status === 401) return "用户名或密码错误";
  }
  return fallback;
}

function LegalLink({
  docId,
  children,
  onOpen,
}: {
  docId: LegalDocId;
  children: string;
  onOpen: (id: LegalDocId) => void;
}) {
  return (
    <button
      type="button"
      className="text-foreground underline-offset-2 hover:underline"
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onOpen(docId);
      }}
    >
      {children}
    </button>
  );
}

function revealError(message: string | null, reveal: boolean): string | null {
  return reveal ? message : null;
}

export function LoginPage() {
  const setAuthenticated = useAuthStore((s) => s.setAuthenticated);
  const [mode, setMode] = useState<Mode>("login");
  const [identifier, setIdentifier] = useState(() => loadRememberedUsername());
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [nickname, setNickname] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [isAdult, setIsAdult] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeKind, setNoticeKind] = useState<"success" | "hint">("hint");
  const [busy, setBusy] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [legalDoc, setLegalDoc] = useState<LegalDocId | null>(null);
  const [forgotStep, setForgotStep] = useState<ForgotStep>("email");
  const [sendAttempted, setSendAttempted] = useState(false);
  const [registerCodeSent, setRegisterCodeSent] = useState(false);
  const [registerCodeExpiresIn, setRegisterCodeExpiresIn] = useState<
    number | null
  >(null);
  const registerIdentityTick = useRef(0);
  const resend = useResendCountdown();

  const identifierErr = loginIdentifierError(identifier);
  const loginPasswordErr = passwordFieldError(password, 1);
  const registerEmailErr = emailFieldError(email);
  const registerPasswordErr = passwordFieldError(
    password,
    MIN_PASSWORD_LENGTH,
    "请设置密码",
  );
  const adultErr = isAdult ? null : "请确认已年满 18 周岁";
  const agreedErr = agreed ? null : "请同意用户协议和隐私政策";
  const codeErr = emailCodeError(code);
  const forgotEmailErr = emailFieldError(email);
  const resetPasswordErr = passwordFieldError(
    newPassword,
    MIN_PASSWORD_LENGTH,
    "请设置新密码",
  );
  const confirmErr =
    confirmPassword.length > 0 && newPassword !== confirmPassword
      ? "两次输入的新密码不一致"
      : attempted && !confirmPassword
        ? "请再次输入新密码"
        : newPassword && confirmPassword && newPassword !== confirmPassword
          ? "两次输入的新密码不一致"
          : null;

  const loginReady = !identifierErr && !loginPasswordErr;
  const registerSendReady = !registerEmailErr && !registerPasswordErr;
  const registerReady =
    registerSendReady &&
    registerCodeSent &&
    !codeErr &&
    !adultErr &&
    !agreedErr;
  const forgotReady = !forgotEmailErr;
  const resetReady =
    !codeErr &&
    !resetPasswordErr &&
    Boolean(confirmPassword) &&
    newPassword === confirmPassword;

  const loginIdentifierDirty = attempted || identifier.length > 0;
  const loginPasswordDirty = attempted || password.length > 0;
  const registerEmailDirty = sendAttempted || attempted || email.length > 0;
  const registerPasswordDirty =
    sendAttempted || attempted || password.length > 0;
  const codeDirty = attempted || code.length > 0;
  const forgotDirty = attempted || email.length > 0;
  const resetCodeDirty = attempted || code.length > 0;
  const newPasswordDirty = attempted || newPassword.length > 0;
  const registerCodeFieldErr =
    attempted && !registerCodeSent
      ? "请先获取验证码"
      : revealError(codeErr, codeDirty);
  const getCodeCooling = registerCodeSent && !resend.canResend;
  const getCodeLabel = getCodeCooling
    ? `重新发送（${resend.left}s）`
    : busy
      ? "请稍候…"
      : registerCodeSent
        ? "重新发送"
        : "获取验证码";

  const invalidateRegisterCode = () => {
    registerIdentityTick.current += 1;
    setCode("");
    setRegisterCodeSent(false);
    setRegisterCodeExpiresIn(null);
    setNotice(null);
    setError(null);
  };

  const switchMode = (next: Mode) => {
    setMode(next);
    setError(null);
    setNotice(null);
    setNoticeKind("hint");
    setCode("");
    setNickname("");
    setNewPassword("");
    setConfirmPassword("");
    setAttempted(false);
    setSendAttempted(false);
    setRegisterCodeSent(false);
    setRegisterCodeExpiresIn(null);
    registerIdentityTick.current += 1;
    setForgotStep("email");
    if (next === "register" && isLikelyEmail(identifier) && !email.trim()) {
      setEmail(identifier.trim());
    }
  };

  const afterLogin = async (
    user: Awaited<ReturnType<typeof login>>,
    remembered: string,
  ) => {
    saveRememberedUsername(remembered.trim());
    setAuthenticated(user);
    void persistAgentTownSession();
    void cacheShellMeta({ user });
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setAttempted(true);
    if (!loginReady || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const trimmed = identifier.trim();
      await afterLogin(await login(trimmed, password), trimmed);
    } catch (err) {
      const message = errorMessage(err, "登录失败，请重试");
      setError(message);
      if (err instanceof ApiError && err.code === "EMAIL_NOT_VERIFIED") {
        setNoticeKind("hint");
        setNotice("可在账户设置中补发验证码完成验证。");
      }
    } finally {
      setBusy(false);
    }
  };

  const handleSendRegisterCode = async () => {
    setSendAttempted(true);
    if (!registerSendReady || busy) return;
    if (getCodeCooling) return;
    const wasSent = registerCodeSent;
    const tick = registerIdentityTick.current;
    setBusy(true);
    setError(null);
    try {
      const accepted = await sendRegisterCode({
        email: email.trim(),
        password,
      });
      if (tick !== registerIdentityTick.current) return;
      setRegisterCodeSent(true);
      setRegisterCodeExpiresIn(accepted?.expiresIn ?? null);
      setNoticeKind("success");
      resend.start();
      setNotice(
        wasSent ? "验证码已重新发送" : `验证码已发送至 ${email.trim()}`,
      );
    } catch (err) {
      if (tick !== registerIdentityTick.current) return;
      setError(errorMessage(err, "发送验证码失败，请重试"));
    } finally {
      setBusy(false);
    }
  };

  const handleVerifyRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setAttempted(true);
    if (!registerReady || busy) return;
    setBusy(true);
    setError(null);
    try {
      const trimmedEmail = email.trim();
      const trimmedNickname = nickname.trim();
      await verifyRegister(trimmedEmail, code, trimmedNickname || undefined);
      await afterLogin(await login(trimmedEmail, password), trimmedEmail);
    } catch (err) {
      setError(errorMessage(err, "注册失败，请重试"));
    } finally {
      setBusy(false);
    }
  };

  const handleSendForgot = async (e: React.FormEvent) => {
    e.preventDefault();
    setAttempted(true);
    if (!forgotReady || busy) return;
    setBusy(true);
    setError(null);
    try {
      await forgotPassword(email.trim());
      setForgotStep("reset");
      setAttempted(false);
      resend.start();
      setNotice("如果该邮箱已注册，你会收到验证码");
    } catch (err) {
      setError(errorMessage(err, "发送验证码失败，请重试"));
    } finally {
      setBusy(false);
    }
  };

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setAttempted(true);
    if (!resetReady || busy) return;
    setBusy(true);
    setError(null);
    try {
      await resetPassword(email.trim(), code, newPassword);
      setMode("login");
      setForgotStep("email");
      setAttempted(false);
      setCode("");
      setNewPassword("");
      setConfirmPassword("");
      setNoticeKind("success");
      setNotice("密码已重置，请使用新密码登录");
    } catch (err) {
      setError(errorMessage(err, "重置失败，请重试"));
    } finally {
      setBusy(false);
    }
  };

  const handleResendForgot = async () => {
    if (!resend.canResend || busy) return;
    setBusy(true);
    setError(null);
    try {
      await forgotPassword(email.trim());
      resend.start();
      setNotice("如果该邮箱已注册，你会收到验证码");
    } catch (err) {
      setError(errorMessage(err, "重发失败，请重试"));
    } finally {
      setBusy(false);
    }
  };

  if (legalDoc) {
    return <LegalDocPane docId={legalDoc} onBack={() => setLegalDoc(null)} />;
  }

  return (
    <div className="flex h-full w-full items-center justify-center bg-background p-6">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <BrandMark
            size="md"
            layout="stack"
            className="w-full items-center text-foreground"
          />
          <p className="mt-2 text-sm text-muted-foreground">协作智能平台</p>
        </div>

        {mode !== "forgot" && (
          <div
            className="mb-4 flex gap-1 rounded-lg bg-muted p-1"
            role="tablist"
            aria-label="登录或注册"
          >
            {(["login", "register"] as const).map((m) => (
              <Button
                key={m}
                variant="ghost"
                role="tab"
                aria-selected={mode === m}
                onClick={() => switchMode(m)}
                className={`h-8 flex-1 rounded-lg text-sm ${
                  mode === m
                    ? "bg-card text-foreground shadow-sm hover:bg-card"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {m === "login" ? "登录" : "注册"}
              </Button>
            ))}
          </div>
        )}

        {mode === "login" && (
          <form noValidate onSubmit={handleLogin} className="space-y-3">
            <div className="space-y-1">
              <input
                className={inputClass}
                placeholder="邮箱或用户名"
                autoComplete="username"
                value={identifier}
                aria-invalid={
                  Boolean(revealError(identifierErr, loginIdentifierDirty)) ||
                  undefined
                }
                onChange={(e) => setIdentifier(e.target.value)}
              />
              <FieldError>
                {revealError(identifierErr, loginIdentifierDirty)}
              </FieldError>
            </div>
            <div className="space-y-1">
              <PasswordField
                placeholder="密码"
                autoComplete="current-password"
                value={password}
                invalid={Boolean(
                  revealError(loginPasswordErr, loginPasswordDirty),
                )}
                onChange={setPassword}
              />
              <FieldError>
                {revealError(loginPasswordErr, loginPasswordDirty)}
              </FieldError>
            </div>
            {error && <AuthOutcome kind="error">{error}</AuthOutcome>}
            {notice && <AuthOutcome kind={noticeKind}>{notice}</AuthOutcome>}
            <Button type="submit" className="h-10 w-full" disabled={busy}>
              {busy ? "请稍候…" : "登录"}
            </Button>
            <p className="pt-1 text-center text-xs text-muted-foreground">
              <button
                type="button"
                className="text-foreground underline-offset-2 hover:underline"
                onClick={() => switchMode("forgot")}
              >
                忘记密码？
              </button>
            </p>
          </form>
        )}

        {mode === "register" && (
          <form
            noValidate
            onSubmit={handleVerifyRegister}
            className="space-y-3"
          >
            <div className="space-y-1">
              <input
                className={inputClass}
                type="email"
                placeholder="邮箱"
                autoComplete="email"
                value={email}
                aria-invalid={
                  Boolean(revealError(registerEmailErr, registerEmailDirty)) ||
                  undefined
                }
                onChange={(e) => {
                  setEmail(e.target.value);
                  invalidateRegisterCode();
                }}
              />
              <FieldError>
                {revealError(registerEmailErr, registerEmailDirty)}
              </FieldError>
            </div>
            <div className="space-y-1">
              <PasswordField
                placeholder="密码（至少 8 位）"
                autoComplete="new-password"
                value={password}
                invalid={Boolean(
                  revealError(registerPasswordErr, registerPasswordDirty),
                )}
                onChange={(value) => {
                  setPassword(value);
                  invalidateRegisterCode();
                }}
              />
              <FieldError>
                {revealError(registerPasswordErr, registerPasswordDirty)}
              </FieldError>
            </div>
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <input
                  className={inputClass}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  placeholder="验证码（6 位）"
                  aria-label="验证码（6 位）"
                  value={code}
                  aria-invalid={Boolean(registerCodeFieldErr) || undefined}
                  onChange={(e) => setCode(normalizeEmailCode(e.target.value))}
                />
                <Button
                  type="button"
                  variant="neutral"
                  className="h-10 shrink-0 whitespace-nowrap border border-input px-3"
                  disabled={busy || getCodeCooling}
                  onClick={() => void handleSendRegisterCode()}
                >
                  {getCodeLabel}
                </Button>
              </div>
              <FieldError>{registerCodeFieldErr}</FieldError>
              {registerCodeSent && registerCodeExpiresIn != null && (
                <p className="text-xs text-muted-foreground">
                  {formatEmailCodeValidityHint(registerCodeExpiresIn)}
                </p>
              )}
            </div>
            <div className="space-y-1">
              <input
                className={inputClass}
                placeholder="昵称（选填）"
                aria-label="昵称（选填）"
                autoComplete="nickname"
                value={nickname}
                maxLength={200}
                onChange={(e) => setNickname(e.target.value)}
              />
            </div>
            <div className="space-y-1 pt-0.5 text-xs leading-snug text-muted-foreground">
              <label className="flex gap-2">
                <input
                  type="checkbox"
                  className={checkClass}
                  checked={isAdult}
                  onChange={(e) => setIsAdult(e.target.checked)}
                />
                <span>我已年满 18 周岁</span>
              </label>
              <FieldError>{revealError(adultErr, attempted)}</FieldError>
              <label className="flex gap-2">
                <input
                  type="checkbox"
                  className={checkClass}
                  checked={agreed}
                  onChange={(e) => setAgreed(e.target.checked)}
                />
                <span>
                  我已阅读并同意
                  <LegalLink docId="terms" onOpen={setLegalDoc}>
                    《用户协议》
                  </LegalLink>
                  和
                  <LegalLink docId="privacy" onOpen={setLegalDoc}>
                    《隐私政策》
                  </LegalLink>
                </span>
              </label>
              <FieldError>{revealError(agreedErr, attempted)}</FieldError>
            </div>
            {error && <AuthOutcome kind="error">{error}</AuthOutcome>}
            {notice && <AuthOutcome kind={noticeKind}>{notice}</AuthOutcome>}
            <Button type="submit" className="h-10 w-full" disabled={busy}>
              {busy ? "请稍候…" : "注册"}
            </Button>
          </form>
        )}

        {mode === "forgot" && forgotStep === "email" && (
          <form noValidate onSubmit={handleSendForgot} className="space-y-3">
            <p className="text-sm text-muted-foreground">
              输入注册邮箱，我们会发送验证码。
            </p>
            <div className="space-y-1">
              <input
                className={inputClass}
                type="email"
                placeholder="邮箱"
                autoComplete="email"
                value={email}
                aria-invalid={
                  Boolean(revealError(forgotEmailErr, forgotDirty)) || undefined
                }
                onChange={(e) => setEmail(e.target.value)}
              />
              <FieldError>
                {revealError(forgotEmailErr, forgotDirty)}
              </FieldError>
            </div>
            {error && <AuthOutcome kind="error">{error}</AuthOutcome>}
            <Button type="submit" className="h-10 w-full" disabled={busy}>
              {busy ? "请稍候…" : "发送验证码"}
            </Button>
            <p className="pt-1 text-center text-xs text-muted-foreground">
              <button
                type="button"
                className="text-foreground underline-offset-2 hover:underline"
                onClick={() => switchMode("login")}
              >
                返回登录
              </button>
            </p>
          </form>
        )}

        {mode === "forgot" && forgotStep === "reset" && (
          <form noValidate onSubmit={handleResetPassword} className="space-y-3">
            {notice ? (
              <AuthOutcome kind="success">{notice}</AuthOutcome>
            ) : (
              <AuthOutcome kind="success">{`验证码已发送至 ${email.trim()}`}</AuthOutcome>
            )}
            <div className="space-y-1">
              <input
                className={inputClass}
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="验证码（6 位）"
                aria-label="验证码（6 位）"
                value={code}
                aria-invalid={
                  Boolean(revealError(codeErr, resetCodeDirty)) || undefined
                }
                onChange={(e) => setCode(normalizeEmailCode(e.target.value))}
              />
              <FieldError>{revealError(codeErr, resetCodeDirty)}</FieldError>
            </div>
            <div className="space-y-1">
              <PasswordField
                placeholder="新密码（至少 8 位）"
                autoComplete="new-password"
                value={newPassword}
                invalid={Boolean(
                  revealError(resetPasswordErr, newPasswordDirty),
                )}
                onChange={setNewPassword}
              />
              <FieldError>
                {revealError(resetPasswordErr, newPasswordDirty)}
              </FieldError>
            </div>
            <div className="space-y-1">
              <PasswordField
                placeholder="确认新密码"
                autoComplete="new-password"
                value={confirmPassword}
                invalid={Boolean(confirmErr)}
                onChange={setConfirmPassword}
              />
              <FieldError>{confirmErr}</FieldError>
            </div>
            {error && <AuthOutcome kind="error">{error}</AuthOutcome>}
            <Button type="submit" className="h-10 w-full" disabled={busy}>
              {busy ? "请稍候…" : "重置密码"}
            </Button>
            <div className="flex justify-between text-xs text-muted-foreground">
              <button
                type="button"
                className="text-foreground underline-offset-2 hover:underline"
                onClick={() => switchMode("login")}
              >
                返回登录
              </button>
              <button
                type="button"
                className="text-foreground underline-offset-2 hover:underline disabled:opacity-40"
                disabled={!resend.canResend || busy}
                onClick={() => void handleResendForgot()}
              >
                {resend.canResend ? "重新发送" : `重新发送（${resend.left}s）`}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
