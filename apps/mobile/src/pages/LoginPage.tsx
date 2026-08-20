import {
  AuthApiError,
  forgotPassword,
  login,
  resetPassword,
  sendRegisterCode,
  verifyRegister,
} from "@/api/auth";
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
  getRememberedUsername,
  setRememberedUsername,
} from "@/lib/rememberedUsername";
import type { LegalDocId } from "@/pages/legal/types";
import { CheckCircle2, CircleAlert, Eye, EyeOff, Info } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

type Mode = "login" | "register" | "forgot";
type ForgotStep = "email" | "reset";

function legalPath(id: LegalDocId): string {
  return `/legal/${id}`;
}

/** Relative in-app path only — blocks `//…`, absolute URLs, and `/login` loops. */
function safeReturnPath(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  if (!raw.startsWith("/") || raw.startsWith("//")) return null;
  if (raw.includes("://")) return null;
  if (raw === "/login" || raw.startsWith("/login?")) return null;
  return raw;
}

function revealError(message: string | null, reveal: boolean): string | null {
  return reveal ? message : null;
}

function FieldError({ children }: { children: string | null }) {
  if (!children) return null;
  return (
    <p className="auth-field-error" role="alert">
      {children}
    </p>
  );
}

function AuthOutcome({
  kind,
  children,
}: {
  kind: "error" | "success" | "hint";
  children: string;
}) {
  const Icon =
    kind === "error" ? CircleAlert : kind === "success" ? CheckCircle2 : Info;
  const label =
    kind === "error" ? "错误" : kind === "success" ? "成功" : "提示";
  if (kind === "error") {
    return (
      <div className="error auth-outcome" role="alert">
        <Icon size={14} aria-hidden />
        <span className="sr-only">{label}：</span>
        <span>{children}</span>
      </div>
    );
  }
  return (
    <output className={`auth-outcome${kind === "success" ? " ok" : ""}`}>
      <Icon size={14} aria-hidden />
      <span className="sr-only">{label}：</span>
      <span>{children}</span>
    </output>
  );
}

function PasswordField({
  value,
  onChange,
  placeholder,
  autoComplete,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  autoComplete?: string;
  disabled?: boolean;
}) {
  const [reveal, setReveal] = useState(false);
  return (
    <div className="auth-password">
      <input
        placeholder={placeholder}
        type={reveal ? "text" : "password"}
        value={value}
        autoComplete={autoComplete}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        type="button"
        className="auth-password-toggle"
        aria-label={reveal ? "隐藏密码" : "显示密码"}
        onClick={() => setReveal((v) => !v)}
      >
        {reveal ? <EyeOff size={18} /> : <Eye size={18} />}
      </button>
    </div>
  );
}

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const from = safeReturnPath(
    (location.state as { from?: unknown } | null)?.from,
  );
  const [mode, setMode] = useState<Mode>("login");
  const [identifier, setIdentifier] = useState(
    () => getRememberedUsername() ?? "",
  );
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
  const [sendAttempted, setSendAttempted] = useState(false);
  const [registerCodeSent, setRegisterCodeSent] = useState(false);
  const [registerCodeExpiresIn, setRegisterCodeExpiresIn] = useState<
    number | null
  >(null);
  const [forgotStep, setForgotStep] = useState<ForgotStep>("email");
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
        : null;

  const loginReady = !identifierErr && !loginPasswordErr;
  const registerSendReady = !registerEmailErr && !registerPasswordErr;
  const registerSubmitReady =
    registerSendReady &&
    !codeErr &&
    !adultErr &&
    !agreedErr &&
    registerCodeSent;
  const registerSendCooling = registerCodeSent && !resend.canResend;
  const getCodeLabel = registerSendCooling
    ? `重新发送（${resend.left}s）`
    : busy
      ? "请稍候…"
      : registerCodeSent
        ? "重新发送"
        : "获取验证码";
  const forgotReady = !forgotEmailErr;
  const resetReady =
    !codeErr &&
    !resetPasswordErr &&
    Boolean(confirmPassword) &&
    newPassword === confirmPassword;

  const loginIdentifierDirty = attempted || identifier.length > 0;
  const loginPasswordDirty = attempted || password.length > 0;
  const registerEmailDirty = attempted || sendAttempted || email.length > 0;
  const registerPasswordDirty =
    attempted || sendAttempted || password.length > 0;
  const codeDirty = attempted || code.length > 0;
  const registerCodeFieldErr =
    attempted && !registerCodeSent
      ? "请先获取验证码"
      : revealError(codeErr, codeDirty);
  const forgotDirty = attempted || email.length > 0;
  const resetCodeDirty = attempted || code.length > 0;
  const newPasswordDirty = attempted || newPassword.length > 0;

  function switchMode(next: Mode) {
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
    setForgotStep("email");
    if (next === "register" && isLikelyEmail(identifier) && !email.trim()) {
      setEmail(identifier.trim());
    }
  }

  async function afterLogin(trimmed: string) {
    setRememberedUsername(trimmed);
    navigate(from ?? "/", { replace: true });
  }

  async function onLogin(e: FormEvent) {
    e.preventDefault();
    setAttempted(true);
    if (!loginReady || busy) return;
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const trimmed = identifier.trim();
      await login(trimmed, password);
      await afterLogin(trimmed);
    } catch (err) {
      const message = err instanceof Error ? err.message : "登录失败，请重试";
      setError(message);
      if (err instanceof AuthApiError && err.code === "EMAIL_NOT_VERIFIED") {
        setNoticeKind("hint");
        setNotice("可在账户设置中补发验证码完成验证。");
      }
    } finally {
      setBusy(false);
    }
  }

  function invalidateRegisterCode() {
    setCode("");
    setRegisterCodeSent(false);
    setRegisterCodeExpiresIn(null);
    setNotice(null);
    setError(null);
  }

  async function onSendRegisterCode() {
    setSendAttempted(true);
    if (!registerSendReady || busy || registerSendCooling) return;
    setError(null);
    setNotice(null);
    const wasSent = registerCodeSent;
    setBusy(true);
    try {
      const sent = await sendRegisterCode({
        email: email.trim(),
        password,
      });
      setRegisterCodeSent(true);
      setRegisterCodeExpiresIn(sent?.expiresIn ?? null);
      resend.start();
      setNoticeKind("success");
      setNotice(
        wasSent ? "验证码已重新发送" : `验证码已发送至 ${email.trim()}`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送验证码失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function onVerifyRegister(e: FormEvent) {
    e.preventDefault();
    setAttempted(true);
    if (!registerSubmitReady || busy) return;
    setError(null);
    setBusy(true);
    try {
      const trimmedEmail = email.trim();
      const trimmedNickname = nickname.trim();
      await verifyRegister(trimmedEmail, code, trimmedNickname || undefined);
      await login(trimmedEmail, password);
      await afterLogin(trimmedEmail);
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function onSendForgot(e: FormEvent) {
    e.preventDefault();
    setAttempted(true);
    if (!forgotReady || busy) return;
    setError(null);
    setBusy(true);
    try {
      await forgotPassword(email.trim());
      setForgotStep("reset");
      setAttempted(false);
      resend.start();
      setNotice("如果该邮箱已注册，你会收到验证码");
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送验证码失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function onResetPassword(e: FormEvent) {
    e.preventDefault();
    setAttempted(true);
    if (!resetReady || busy) return;
    setError(null);
    setBusy(true);
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
      setError(err instanceof Error ? err.message : "重置失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function onResendForgot() {
    if (!resend.canResend || busy) return;
    setError(null);
    setBusy(true);
    try {
      await forgotPassword(email.trim());
      resend.start();
      setNotice("如果该邮箱已注册，你会收到验证码");
    } catch (err) {
      setError(err instanceof Error ? err.message : "重发失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="screen center">
      <div className="auth-wrap">
        <div className="auth-header">
          <h1>AgentCore</h1>
          <p className="muted">你的 Multi-Agent AI 工作台</p>
        </div>

        {mode !== "forgot" && (
          <div className="auth-seg" role="tablist" aria-label="登录或注册">
            {(["login", "register"] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={mode === m}
                className={`auth-seg-btn${mode === m ? " auth-seg-active" : ""}`}
                onClick={() => switchMode(m)}
              >
                {m === "login" ? "登录" : "注册"}
              </button>
            ))}
          </div>
        )}

        {mode === "login" && (
          <form className="card auth-card" noValidate onSubmit={onLogin}>
            <input
              placeholder="邮箱或用户名"
              value={identifier}
              autoComplete="username"
              disabled={busy}
              onChange={(e) => setIdentifier(e.target.value)}
            />
            <FieldError>
              {revealError(identifierErr, loginIdentifierDirty)}
            </FieldError>
            <PasswordField
              placeholder="密码"
              value={password}
              autoComplete="current-password"
              disabled={busy}
              onChange={setPassword}
            />
            <FieldError>
              {revealError(loginPasswordErr, loginPasswordDirty)}
            </FieldError>
            {error && <AuthOutcome kind="error">{error}</AuthOutcome>}
            {notice && <AuthOutcome kind={noticeKind}>{notice}</AuthOutcome>}
            <button type="submit" disabled={busy}>
              {busy ? "请稍候…" : "登录"}
            </button>
            <p className="auth-foot muted">
              <button
                type="button"
                className="link"
                onClick={() => switchMode("forgot")}
              >
                忘记密码？
              </button>
            </p>
          </form>
        )}

        {mode === "register" && (
          <form
            className="card auth-card"
            noValidate
            onSubmit={onVerifyRegister}
          >
            <input
              placeholder="邮箱"
              type="email"
              value={email}
              autoComplete="email"
              disabled={busy}
              onChange={(e) => {
                setEmail(e.target.value);
                invalidateRegisterCode();
              }}
            />
            <FieldError>
              {revealError(registerEmailErr, registerEmailDirty)}
            </FieldError>
            <PasswordField
              placeholder="密码（至少 8 位）"
              value={password}
              autoComplete="new-password"
              disabled={busy}
              onChange={(value) => {
                setPassword(value);
                invalidateRegisterCode();
              }}
            />
            <FieldError>
              {revealError(registerPasswordErr, registerPasswordDirty)}
            </FieldError>
            <div className="auth-code-row">
              <input
                placeholder="验证码（6 位）"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                disabled={busy}
                onChange={(e) => setCode(normalizeEmailCode(e.target.value))}
              />
              <button
                type="button"
                disabled={busy || registerSendCooling}
                onClick={() => void onSendRegisterCode()}
              >
                {getCodeLabel}
              </button>
            </div>
            <FieldError>{registerCodeFieldErr}</FieldError>
            {registerCodeSent && registerCodeExpiresIn != null && (
              <p className="muted auth-code-hint">
                {formatEmailCodeValidityHint(registerCodeExpiresIn)}
              </p>
            )}
            <input
              placeholder="昵称（选填）"
              value={nickname}
              maxLength={200}
              autoComplete="nickname"
              disabled={busy}
              onChange={(e) => setNickname(e.target.value)}
            />
            <div className="auth-legal">
              <label className="auth-check">
                <input
                  type="checkbox"
                  checked={isAdult}
                  disabled={busy}
                  onChange={(e) => setIsAdult(e.target.checked)}
                />
                <span>我已年满 18 周岁</span>
              </label>
              <FieldError>{revealError(adultErr, attempted)}</FieldError>
              <label className="auth-check">
                <input
                  type="checkbox"
                  checked={agreed}
                  disabled={busy}
                  onChange={(e) => setAgreed(e.target.checked)}
                />
                <span>
                  我已阅读并同意
                  <Link to={legalPath("terms")}>《用户协议》</Link>和
                  <Link to={legalPath("privacy")}>《隐私政策》</Link>
                </span>
              </label>
              <FieldError>{revealError(agreedErr, attempted)}</FieldError>
            </div>
            {error && <AuthOutcome kind="error">{error}</AuthOutcome>}
            {notice && <AuthOutcome kind={noticeKind}>{notice}</AuthOutcome>}
            <button type="submit" disabled={busy}>
              {busy ? "请稍候…" : "注册"}
            </button>
          </form>
        )}

        {mode === "forgot" && forgotStep === "email" && (
          <form className="card auth-card" noValidate onSubmit={onSendForgot}>
            <p className="muted">输入注册邮箱，我们会发送验证码。</p>
            <input
              placeholder="邮箱"
              type="email"
              value={email}
              autoComplete="email"
              disabled={busy}
              onChange={(e) => setEmail(e.target.value)}
            />
            <FieldError>{revealError(forgotEmailErr, forgotDirty)}</FieldError>
            {error && <AuthOutcome kind="error">{error}</AuthOutcome>}
            <button type="submit" disabled={busy}>
              {busy ? "请稍候…" : "发送验证码"}
            </button>
            <p className="auth-foot muted">
              <button
                type="button"
                className="link"
                onClick={() => switchMode("login")}
              >
                返回登录
              </button>
            </p>
          </form>
        )}

        {mode === "forgot" && forgotStep === "reset" && (
          <form
            className="card auth-card"
            noValidate
            onSubmit={onResetPassword}
          >
            <AuthOutcome kind="success">
              {notice ?? `验证码已发送至 ${email.trim()}`}
            </AuthOutcome>
            <input
              placeholder="验证码（6 位）"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              disabled={busy}
              onChange={(e) => setCode(normalizeEmailCode(e.target.value))}
            />
            <FieldError>{revealError(codeErr, resetCodeDirty)}</FieldError>
            <PasswordField
              placeholder="新密码（至少 8 位）"
              value={newPassword}
              autoComplete="new-password"
              disabled={busy}
              onChange={setNewPassword}
            />
            <FieldError>
              {revealError(resetPasswordErr, newPasswordDirty)}
            </FieldError>
            <PasswordField
              placeholder="确认新密码"
              value={confirmPassword}
              autoComplete="new-password"
              disabled={busy}
              onChange={setConfirmPassword}
            />
            <FieldError>{confirmErr}</FieldError>
            {error && <AuthOutcome kind="error">{error}</AuthOutcome>}
            <button type="submit" disabled={busy}>
              {busy ? "请稍候…" : "重置密码"}
            </button>
            <p className="auth-foot muted">
              <button
                type="button"
                className="link"
                onClick={() => switchMode("login")}
              >
                返回登录
              </button>
              {" · "}
              <button
                type="button"
                className="link"
                disabled={!resend.canResend || busy}
                onClick={() => void onResendForgot()}
              >
                {resend.canResend ? "重新发送" : `重新发送（${resend.left}s）`}
              </button>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
