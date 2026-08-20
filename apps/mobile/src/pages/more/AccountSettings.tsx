import {
  AVATAR_MAX_BYTES,
  changePassword,
  deleteAccount,
  deleteAvatar,
  sendEmailCode,
  updateProfile,
  uploadAvatar,
  verifyEmail,
} from "@/api/account";
import { type User, logout, me } from "@/api/auth";
import { getTokens } from "@/api/client";
import {
  type SessionSummary,
  listSessions,
  revokeOtherSessions,
  revokeSession,
} from "@/api/sessions";
import {
  EMAIL_CODE_LENGTH,
  isGeneratedHandle,
  isLikelyEmail,
  isSystemUsernameHandle,
  normalizeEmailCode,
  useResendCountdown,
  usernameFieldError,
} from "@/lib/emailAuth";
import { copyText } from "@/lib/messageExport";
import { Avatar } from "@/pages/more/Avatar";
import {
  formatDeviceLabel,
  formatRelativeTime,
} from "@/pages/more/sessionDisplay";
// 账户设置 (/more/account) — profile / password / avatar / 登录设备 / 注销.
//
// Independent sections, each posting on its own. No global auth store on mobile, so
// the page loads `me()` on open and keeps the user in local state, re-syncing it after
// each mutation that returns the refreshed user.
import {
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";
import "@/pages/more/more.css";

export function AccountSettings() {
  const navigate = useNavigate();
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    me()
      .then((u) => !cancelled && setUser(u))
      .catch(() => {
        if (!getTokens()) navigate("/login", { replace: true });
        else if (!cancelled) setError("加载账户失败");
      });
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  return (
    <div className="screen">
      <header className="bar">
        <button
          type="button"
          className="link"
          onClick={() => navigate("/more")}
        >
          ← 设置
        </button>
        <span>账户设置</span>
        <span style={{ width: 44 }} />
      </header>

      <div className="settings-body">
        {error && <p className="error hint">{error}</p>}
        {user && (
          <>
            <AvatarSection user={user} onUser={setUser} />
            <ProfileSection user={user} onUser={setUser} />
            <PasswordSection />
            <SessionsSection
              onSignedOut={async () => {
                await logout().catch(() => {});
                navigate("/login", { replace: true });
              }}
            />
            <DangerSection
              onDeleted={async () => {
                await logout().catch(() => {});
                navigate("/login", { replace: true });
              }}
            />
          </>
        )}
      </div>
    </div>
  );
}

function Section({
  title,
  note,
  danger,
  children,
}: {
  title: string;
  note?: string;
  danger?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="section">
      <h2 className={`section-title${danger ? " danger" : ""}`}>{title}</h2>
      {note && <p className="section-note">{note}</p>}
      <div className={`section-card${danger ? " danger" : ""}`}>{children}</div>
    </section>
  );
}

function AvatarSection({
  user,
  onUser,
}: { user: User; onUser: (u: User) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // let the user re-pick the same file after an error
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("请选择图片文件");
      return;
    }
    if (file.size > AVATAR_MAX_BYTES) {
      setError("图片不能超过 5 MB");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onUser(await uploadAvatar(file));
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      onUser(await deleteAvatar());
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="头像" note="建议使用清晰的正方形图片。">
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <Avatar user={user} size={64} />
        <div className="btn-row">
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={busy}
          >
            {busy ? "处理中…" : "上传头像"}
          </button>
          {user.avatar_url && (
            <button
              type="button"
              className="btn-outline"
              onClick={() => void remove()}
              disabled={busy}
            >
              移除
            </button>
          )}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          style={{ display: "none" }}
          onChange={(e) => void onFile(e)}
        />
      </div>
      {error && <p className="error">{error}</p>}
    </Section>
  );
}

function emailStatusLabel(
  email: string | null | undefined,
  verifiedAt: string | null | undefined,
): string {
  if (verifiedAt) return "已验证";
  if (email) return "未验证";
  return "未填写";
}

function ProfileSection({
  user,
  onUser,
}: { user: User; onUser: (u: User) => void }) {
  const [displayName, setDisplayName] = useState(user.display_name ?? "");
  const [username, setUsername] = useState(user.username);
  const [email, setEmail] = useState(user.email ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const trimmedName = displayName.trim();
  const trimmedUsername = username.trim().toLowerCase();
  const trimmedEmail = email.trim();
  const usernameErr =
    trimmedUsername === user.username.trim().toLowerCase()
      ? null
      : usernameFieldError(username);
  const dirty =
    trimmedName !== (user.display_name ?? "") ||
    trimmedUsername !== user.username.trim().toLowerCase() ||
    trimmedEmail !== (user.email ?? "");
  const canSave = dirty && trimmedName.length > 0 && !usernameErr && !saving;
  const verifiedAt = user.email_verified_at ?? null;
  const needsCatchup = !verifiedAt;
  const systemHandle = isSystemUsernameHandle(user.username);

  async function copyUsername() {
    if (await copyText(user.username)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const update: {
        display_name: string;
        email: string;
        username?: string;
      } = {
        display_name: trimmedName,
        email: trimmedEmail,
      };
      if (trimmedUsername !== user.username.trim().toLowerCase()) {
        update.username = trimmedUsername;
      }
      const updated = await updateProfile(update);
      onUser(updated);
      setDisplayName(updated.display_name);
      setUsername(updated.username);
      setEmail(updated.email ?? "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Section
      title="个人资料"
      note="昵称会展示给团队成员。用户名是找人码，别人用它精确搜索你。邮箱用于找回密码；未验证不影响登录。更改邮箱后需重新验证，不会自动发送验证码。"
    >
      <div className="field">
        <span className="field-label">昵称</span>
        <input
          value={displayName}
          maxLength={200}
          placeholder="你的昵称"
          onChange={(e) => setDisplayName(e.target.value)}
        />
        {isGeneratedHandle(user.username, user.display_name) && (
          <p className="hint">
            当前昵称是系统分配的找人码。改成你希望别人看到的名字即可，随时能再改。
          </p>
        )}
      </div>
      <div className="field">
        <span className="field-label">用户名</span>
        <div className="field-inline">
          <input
            value={username}
            maxLength={32}
            autoComplete="username"
            aria-label="用户名"
            spellCheck={false}
            onChange={(e) => setUsername(e.target.value)}
          />
          <button
            type="button"
            className="btn-outline btn-sm"
            aria-label="复制用户名"
            onClick={() => void copyUsername()}
          >
            {copied ? "已复制" : "复制"}
          </button>
        </div>
        {usernameErr && <p className="error">{usernameErr}</p>}
        {systemHandle ? (
          <p className="hint">
            这是系统分配的找人码。可改成你希望别人用来搜索你的用户名，随时能再改。
          </p>
        ) : (
          <p className="hint">自选用户名后 14 天内不能再次修改。</p>
        )}
      </div>
      <div className="field">
        <span className="field-label">
          邮箱 · {emailStatusLabel(user.email, verifiedAt)}
        </span>
        <input
          type="email"
          value={email}
          maxLength={255}
          placeholder="you@example.com"
          autoComplete="email"
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>
      {needsCatchup && (
        <EmailCatchup
          email={trimmedEmail || user.email || ""}
          onVerified={(updated) => {
            onUser(updated);
            setEmail(updated.email ?? "");
          }}
        />
      )}
      {error && <p className="error">{error}</p>}
      <div className="field-actions">
        <button type="button" disabled={!canSave} onClick={() => void save()}>
          {saving ? "保存中…" : "保存"}
        </button>
      </div>
    </Section>
  );
}

function EmailCatchup({
  email,
  onVerified,
}: {
  email: string;
  onVerified: (user: User) => void;
}) {
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const resend = useResendCountdown();
  const canSend = isLikelyEmail(email) && !busy && resend.canResend;
  const canVerify = code.length === EMAIL_CODE_LENGTH && !busy;

  async function send() {
    if (!canSend) return;
    setBusy(true);
    setError(null);
    try {
      await sendEmailCode(email.trim());
      setSent(true);
      resend.start();
    } catch (e) {
      setError(e instanceof Error ? e.message : "发送验证码失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    if (!canVerify) return;
    setBusy(true);
    setError(null);
    try {
      onVerified(await verifyEmail(email.trim(), code));
      setCode("");
      setSent(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "验证失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="field">
      <p className="section-note">
        {email
          ? "验证邮箱后可用于找回密码。未验证不影响登录。"
          : "填写邮箱后即可发送验证码。未验证不影响登录。"}
      </p>
      {sent && (
        <input
          placeholder="验证码（6 位）"
          inputMode="numeric"
          autoComplete="one-time-code"
          value={code}
          onChange={(e) => setCode(normalizeEmailCode(e.target.value))}
        />
      )}
      {error && <p className="error">{error}</p>}
      <div className="field-actions">
        <button type="button" disabled={!canSend} onClick={() => void send()}>
          {busy && !sent
            ? "发送中…"
            : resend.canResend
              ? sent
                ? "重新发送"
                : "发送验证码"
              : `重新发送（${resend.left}s）`}
        </button>
        {sent && (
          <button
            type="button"
            disabled={!canVerify}
            onClick={() => void verify()}
          >
            {busy ? "验证中…" : "验证"}
          </button>
        )}
      </div>
    </div>
  );
}

function PasswordSection() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const localError =
    next.length > 0 && next.length < 8
      ? "新密码至少需要 8 个字符"
      : confirm.length > 0 && next !== confirm
        ? "两次输入的新密码不一致"
        : null;
  const canSave =
    current.length > 0 && next.length >= 8 && next === confirm && !saving;

  async function save() {
    setSaving(true);
    setError(null);
    setDone(false);
    try {
      await changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      setDone(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "修改失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Section title="修改密码" note="修改后，除当前设备外的所有登录都会失效。">
      <div className="field">
        <span className="field-label">当前密码</span>
        <input
          type="password"
          value={current}
          autoComplete="current-password"
          onChange={(e) => setCurrent(e.target.value)}
        />
      </div>
      <div className="field">
        <span className="field-label">新密码（至少 8 位）</span>
        <input
          type="password"
          value={next}
          autoComplete="new-password"
          onChange={(e) => setNext(e.target.value)}
        />
      </div>
      <div className="field">
        <span className="field-label">确认新密码</span>
        <input
          type="password"
          value={confirm}
          autoComplete="new-password"
          onChange={(e) => setConfirm(e.target.value)}
        />
      </div>
      {(localError || error) && <p className="error">{localError ?? error}</p>}
      {done && (
        <p className="section-note" style={{ color: "var(--success)" }}>
          密码已更新，其他设备需重新登录。
        </p>
      )}
      <div className="field-actions">
        <button type="button" disabled={!canSave} onClick={() => void save()}>
          {saving ? "更新中…" : "更新密码"}
        </button>
      </div>
    </Section>
  );
}

function SessionsSection({ onSignedOut }: { onSignedOut: () => void }) {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [confirmOthers, setConfirmOthers] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listSessions();
      setSessions(res.data);
    } catch (e) {
      if (!getTokens()) {
        navigate("/login", { replace: true });
        return;
      }
      setError(e instanceof Error ? e.message : "加载登录设备失败");
      setSessions(null);
    } finally {
      setLoading(false);
    }
  }, [navigate]);

  useEffect(() => {
    void load();
  }, [load]);

  async function doRevoke(session: SessionSummary) {
    setBusyId(session.id);
    setActionError(null);
    try {
      await revokeSession(session.id);
      if (session.current) {
        onSignedOut();
        return;
      }
      setConfirmId(null);
      await load();
    } catch (e) {
      if (!getTokens()) {
        navigate("/login", { replace: true });
        return;
      }
      setActionError(e instanceof Error ? e.message : "退出设备失败");
    } finally {
      setBusyId(null);
    }
  }

  async function doRevokeOthers() {
    setBusyId("__others__");
    setActionError(null);
    try {
      await revokeOtherSessions();
      setConfirmOthers(false);
      await load();
    } catch (e) {
      if (!getTokens()) {
        navigate("/login", { replace: true });
        return;
      }
      setActionError(e instanceof Error ? e.message : "退出其他设备失败");
    } finally {
      setBusyId(null);
    }
  }

  const showOthers = (sessions?.length ?? 0) > 1;
  const busy = busyId !== null;

  return (
    <Section
      title="登录设备"
      note="查看当前活跃的登录会话，可退出不再使用的设备。"
    >
      {loading && sessions === null && <p className="muted hint">加载中…</p>}
      {error && (
        <div>
          <p className="error">{error}</p>
          <div className="field-actions">
            <button
              type="button"
              className="btn-outline"
              onClick={() => void load()}
              disabled={loading}
            >
              重试
            </button>
          </div>
        </div>
      )}
      {!error && sessions && sessions.length === 0 && (
        <p className="section-note">暂无活跃登录设备。</p>
      )}
      {sessions && sessions.length > 0 && (
        <div className="session-list">
          {sessions.map((s) => {
            const confirming = confirmId === s.id;
            return (
              <div key={s.id} className="session-row">
                <div className="session-head">
                  <span className="session-label">
                    {formatDeviceLabel(s.platform, s.user_agent)}
                  </span>
                  {s.current && <span className="session-badge">本机</span>}
                </div>
                <div className="session-meta">
                  {s.ip ? <span>IP {s.ip}</span> : <span>IP 未知</span>}
                  <span>最后活跃 {formatRelativeTime(s.last_used_at)}</span>
                </div>
                {!confirming ? (
                  <div className="session-actions">
                    <button
                      type="button"
                      className="btn-danger-outline btn-sm"
                      disabled={busy}
                      onClick={() => {
                        setConfirmOthers(false);
                        setConfirmId(s.id);
                        setActionError(null);
                      }}
                    >
                      退出
                    </button>
                  </div>
                ) : (
                  <>
                    <p className="section-note">
                      {s.current
                        ? "退出后需要重新登录本机。"
                        : "确认退出该设备？该设备上的登录将立即失效。"}
                    </p>
                    <div className="session-actions">
                      <button
                        type="button"
                        className="btn-outline btn-sm"
                        disabled={busy}
                        onClick={() => {
                          setConfirmId(null);
                          setActionError(null);
                        }}
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        className="btn-danger btn-sm"
                        disabled={busy}
                        onClick={() => void doRevoke(s)}
                      >
                        {busyId === s.id ? "退出中…" : "确认退出"}
                      </button>
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}

      {showOthers &&
        (!confirmOthers ? (
          <div className="field-actions">
            <button
              type="button"
              className="btn-danger-outline"
              disabled={busy}
              onClick={() => {
                setConfirmId(null);
                setConfirmOthers(true);
                setActionError(null);
              }}
            >
              退出其他所有设备
            </button>
          </div>
        ) : (
          <>
            <p className="section-note">
              将退出除本机外的全部登录设备，那些设备需重新登录。
            </p>
            <div className="field-actions">
              <button
                type="button"
                className="btn-outline"
                disabled={busy}
                onClick={() => {
                  setConfirmOthers(false);
                  setActionError(null);
                }}
              >
                取消
              </button>
              <button
                type="button"
                className="btn-danger"
                disabled={busy}
                onClick={() => void doRevokeOthers()}
              >
                {busyId === "__others__" ? "处理中…" : "确认退出其他设备"}
              </button>
            </div>
          </>
        ))}

      {actionError && <p className="error">{actionError}</p>}
    </Section>
  );
}

function DangerSection({ onDeleted }: { onDeleted: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await deleteAccount(password);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : "注销失败，请重试");
      setBusy(false);
    }
  }

  return (
    <Section
      title="危险区域"
      note="注销后账户将被停用并匿名化，且无法恢复。"
      danger
    >
      {!confirming ? (
        <div className="field-actions">
          <button
            type="button"
            className="btn-danger-outline"
            onClick={() => setConfirming(true)}
          >
            注销账户
          </button>
        </div>
      ) : (
        <>
          <p className="section-note">
            输入密码以确认注销，相关对话也会被删除。
          </p>
          <input
            type="password"
            value={password}
            placeholder="当前密码"
            autoComplete="current-password"
            onChange={(e) => {
              setError(null);
              setPassword(e.target.value);
            }}
          />
          {error && <p className="error">{error}</p>}
          <div className="field-actions">
            <button
              type="button"
              className="btn-outline"
              onClick={() => {
                setConfirming(false);
                setPassword("");
                setError(null);
              }}
              disabled={busy}
            >
              取消
            </button>
            <button
              type="button"
              className="btn-danger"
              disabled={busy || password.length === 0}
              onClick={() => void confirm()}
            >
              {busy ? "注销中…" : "确认注销"}
            </button>
          </div>
        </>
      )}
    </Section>
  );
}
