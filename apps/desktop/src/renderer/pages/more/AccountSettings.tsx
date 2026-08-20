import {
  SettingField,
  SettingRow,
  SettingsFormMessage,
  SettingsSection,
  SettingsStack,
} from "@/components/settings";
import { Badge, Button, Card, ConfirmDialog, Input } from "@/components/ui";
import {
  EMAIL_CODE_LENGTH,
  isLikelyEmail,
  isSystemUsername,
  normalizeEmailCode,
  useResendCountdown,
  usernameFieldError,
} from "@/lib/emailAuth";
import { errMsg } from "@/lib/errMsg";
import { notifySuccess } from "@/lib/toast";
import {
  changePassword,
  deleteAccount,
  deleteAvatar,
  sendEmailCode,
  updateProfile,
  uploadAvatar,
  verifyEmail,
} from "@/services/auth";
import { type AuthUser, useAuthStore } from "@/stores/auth";
import { Loader2 } from "lucide-react";
import { useRef, useState } from "react";
import { LoginSessionsSection } from "./LoginSessionsSection";
import { SettingsHeader } from "./SettingsHeader";

// Mirror of the server's avatar_upload_max_bytes so an oversized pick fails fast,
// before a pointless round-trip.
const AVATAR_MAX_BYTES = 5 * 1024 * 1024;

/**
 * 账户设置 (/more/account) — self-service identity management.
 *
 * Sections: 个人资料 / 修改密码 / 登录设备 / 危险区域 (注销).
 */
export function AccountSettings() {
  return (
    <div>
      <SettingsHeader
        title="账户设置"
        description="管理你的个人资料、登录密码、登录设备与账户。"
      />
      <SettingsStack>
        <AvatarSection />
        <ProfileSection />
        <PasswordSection />
        <LoginSessionsSection />
        <DangerSection />
      </SettingsStack>
    </div>
  );
}

/** 头像: upload a square image or remove; backend re-encodes to square WebP. */
function AvatarSection() {
  const user = useAuthStore((s) => s.user);
  const setAuthenticated = useAuthStore((s) => s.setAuthenticated);
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const initial = (user?.displayName || user?.username || "?")
    .charAt(0)
    .toUpperCase();

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
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
      setAuthenticated(await uploadAvatar(file));
    } catch (err) {
      setError(errMsg(err, "上传失败，请重试"));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      setAuthenticated(await deleteAvatar());
    } catch (err) {
      setError(errMsg(err, "操作失败，请重试"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsSection title="头像" description="上传清晰的正方形图片效果最佳。">
      <Card className="flex items-center gap-4 p-4">
        <div className="flex size-16 shrink-0 items-center justify-center overflow-hidden rounded-full bg-muted text-xl font-medium text-muted-foreground">
          {user?.avatarUrl ? (
            <img
              src={user.avatarUrl}
              alt="头像"
              className="size-16 object-cover"
            />
          ) : (
            initial
          )}
        </div>
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Button
              size="md"
              disabled={busy}
              icon={
                busy ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : undefined
              }
              onClick={() => inputRef.current?.click()}
            >
              上传头像
            </Button>
            {user?.avatarUrl && (
              <Button
                variant="neutral"
                size="md"
                disabled={busy}
                onClick={() => void remove()}
              >
                移除
              </Button>
            )}
          </div>
          <SettingsFormMessage>{error}</SettingsFormMessage>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(e) => void onFile(e)}
        />
      </Card>
    </SettingsSection>
  );
}

function emailStatusLabel(
  email: string | null | undefined,
  verifiedAt: string | null | undefined,
): { label: string; tone: "success" | "muted" } {
  if (verifiedAt) return { label: "已验证", tone: "success" };
  if (email) return { label: "未验证", tone: "muted" };
  return { label: "未填写", tone: "muted" };
}

/** 个人资料: edit nickname + username + email; on save, refresh the auth store. */
function ProfileSection() {
  const user = useAuthStore((s) => s.user);
  const setAuthenticated = useAuthStore((s) => s.setAuthenticated);
  const [displayName, setDisplayName] = useState(user?.displayName ?? "");
  const [username, setUsername] = useState(user?.username ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmedName = displayName.trim();
  const trimmedUsername = username.trim();
  const trimmedEmail = email.trim();
  const nameDirty = trimmedName !== (user?.displayName ?? "");
  const usernameDirty = trimmedUsername !== (user?.username ?? "");
  const emailDirty = trimmedEmail !== (user?.email ?? "");
  const dirty = nameDirty || usernameDirty || emailDirty;
  const nicknameErr = trimmedName.length === 0 ? "昵称不能为空" : null;
  const usernameErr = usernameDirty
    ? usernameFieldError(trimmedUsername)
    : null;
  const canSave =
    dirty && trimmedName.length > 0 && !nicknameErr && !usernameErr && !saving;
  const verifiedAt = user?.emailVerifiedAt ?? null;
  const status = emailStatusLabel(user?.email, verifiedAt);
  const needsCatchup = !verifiedAt;
  const systemUsername = user ? isSystemUsername(user.username) : false;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const patch: {
        displayName?: string;
        username?: string;
        email?: string;
      } = {};
      if (nameDirty) patch.displayName = trimmedName;
      if (usernameDirty) patch.username = trimmedUsername;
      if (emailDirty) patch.email = trimmedEmail;
      const updated = await updateProfile(patch);
      setAuthenticated(updated);
      setDisplayName(updated.displayName);
      setUsername(updated.username);
      setEmail(updated.email ?? "");
    } catch (e) {
      setError(errMsg(e, "保存失败，请重试"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection
      title="个人资料"
      description="昵称会展示给团队成员。用户名是找人码，别人可用它搜到你。邮箱用于找回密码；未验证不影响登录。更改邮箱后需重新验证，不会自动发送验证码。"
    >
      <Card className="space-y-3 p-4">
        <SettingField
          label="昵称"
          htmlFor="account-profile-display-name"
          error={nicknameErr}
        >
          <Input
            id="account-profile-display-name"
            value={displayName}
            maxLength={200}
            placeholder="你的昵称"
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </SettingField>
        <SettingField
          label="用户名"
          htmlFor="account-profile-username"
          hint={
            user && systemUsername
              ? "这是系统分配的找人码。改成你容易记住的名字即可，随时能再改。"
              : user && !systemUsername
                ? "用户名 14 天内只能改一次。"
                : undefined
          }
          error={usernameErr}
        >
          <Input
            id="account-profile-username"
            value={username}
            maxLength={32}
            autoComplete="username"
            onChange={(e) => setUsername(e.target.value)}
          />
        </SettingField>
        <SettingField
          label="邮箱"
          htmlFor="account-profile-email"
          action={<Badge tone={status.tone}>{status.label}</Badge>}
        >
          <Input
            id="account-profile-email"
            type="email"
            value={email}
            maxLength={255}
            placeholder="you@example.com"
            autoComplete="email"
            onChange={(e) => setEmail(e.target.value)}
          />
        </SettingField>
        {needsCatchup && (
          <EmailCatchup
            email={trimmedEmail || user?.email || ""}
            onVerified={(updated) => {
              setAuthenticated(updated);
              setEmail(updated.email ?? "");
            }}
          />
        )}
        <SettingsFormMessage>{error}</SettingsFormMessage>
        <div className="flex justify-end">
          <Button
            size="md"
            disabled={!canSave}
            icon={
              saving ? (
                <Loader2 size={14} className="animate-spin" />
              ) : undefined
            }
            onClick={() => void save()}
          >
            保存
          </Button>
        </div>
      </Card>
    </SettingsSection>
  );
}

/** Logged-in catch-up: send-code + verify. Never blocks the rest of the account page. */
function EmailCatchup({
  email,
  onVerified,
}: {
  email: string;
  onVerified: (user: AuthUser) => void;
}) {
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const resend = useResendCountdown();
  const canSend = isLikelyEmail(email) && !busy && resend.canResend;
  const canVerify = code.length === EMAIL_CODE_LENGTH && !busy;

  const send = async () => {
    if (!canSend) return;
    setBusy(true);
    setError(null);
    try {
      await sendEmailCode(email.trim());
      setSent(true);
      resend.start();
    } catch (e) {
      setError(errMsg(e, "发送验证码失败，请重试"));
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    if (!canVerify) return;
    setBusy(true);
    setError(null);
    try {
      onVerified(await verifyEmail(email.trim(), code));
      setCode("");
      setSent(false);
    } catch (e) {
      setError(errMsg(e, "验证失败，请重试"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 rounded-lg border border-border bg-muted/30 p-3">
      <p className="text-xs text-muted-foreground">
        {email
          ? "验证邮箱后可用于找回密码。未验证不影响登录。"
          : "填写邮箱后即可发送验证码。未验证不影响登录。"}
      </p>
      {sent && (
        <Input
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="验证码（6 位）"
          aria-label="验证码（6 位）"
          value={code}
          onChange={(e) => setCode(normalizeEmailCode(e.target.value))}
        />
      )}
      <SettingsFormMessage>{error}</SettingsFormMessage>
      <div className="flex flex-wrap justify-end gap-2">
        <Button
          size="md"
          variant="neutral"
          disabled={!canSend}
          onClick={() => void send()}
        >
          {busy && !sent
            ? "发送中…"
            : resend.canResend
              ? sent
                ? "重新发送"
                : "发送验证码"
              : `重新发送（${resend.left}s）`}
        </Button>
        {sent && (
          <Button size="md" disabled={!canVerify} onClick={() => void verify()}>
            {busy ? "验证中…" : "验证"}
          </Button>
        )}
      </div>
    </div>
  );
}

/** 修改密码: current + new + confirm; the backend keeps this session alive. */
function PasswordSection() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setCurrent("");
    setNext("");
    setConfirm("");
  };

  // Client-side mirror of the server policy, so obvious mistakes never round-trip.
  const tooShort = next.length > 0 && next.length < 8;
  const mismatch = confirm.length > 0 && next !== confirm;
  const canSave =
    current.length > 0 && next.length >= 8 && next === confirm && !saving;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await changePassword(current, next);
      reset();
      // 其他设备被登出这件事在本机不可见，静默会让用户无法确认是否生效。
      notifySuccess("密码已更新", { description: "其他设备需要重新登录。" });
    } catch (e) {
      setError(errMsg(e, "修改失败，请重试"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection
      title="修改密码"
      description="修改后，除当前设备外的所有登录都会失效。"
    >
      <Card className="space-y-3 p-4">
        <SettingField label="当前密码" htmlFor="account-password-current">
          <Input
            id="account-password-current"
            type="password"
            value={current}
            autoComplete="current-password"
            onChange={(e) => setCurrent(e.target.value)}
          />
        </SettingField>
        <SettingField
          label="新密码（至少 8 位）"
          htmlFor="account-password-new"
          error={tooShort ? "新密码至少需要 8 个字符" : null}
        >
          <Input
            id="account-password-new"
            type="password"
            value={next}
            autoComplete="new-password"
            onChange={(e) => setNext(e.target.value)}
          />
        </SettingField>
        <SettingField
          label="确认新密码"
          htmlFor="account-password-confirm"
          error={mismatch ? "两次输入的新密码不一致" : null}
        >
          <Input
            id="account-password-confirm"
            type="password"
            value={confirm}
            autoComplete="new-password"
            onChange={(e) => setConfirm(e.target.value)}
          />
        </SettingField>
        <SettingsFormMessage>{error}</SettingsFormMessage>
        <div className="flex justify-end">
          <Button
            size="md"
            disabled={!canSave}
            icon={
              saving ? (
                <Loader2 size={14} className="animate-spin" />
              ) : undefined
            }
            onClick={() => void save()}
          >
            更新密码
          </Button>
        </div>
      </Card>
    </SettingsSection>
  );
}

/** 危险区域: irreversible account deletion behind a password-confirm dialog. */
function DangerSection() {
  const [open, setOpen] = useState(false);

  return (
    <SettingsSection
      title="危险区域"
      tone="danger"
      description="注销后账户将被停用并匿名化，且无法恢复。"
    >
      <SettingRow
        className="border-destructive/40 bg-destructive/5"
        label="注销账户"
        description="永久停用此账户，并释放用户名以供重新注册。"
        control={
          <Button
            variant="danger"
            size="md"
            className="shrink-0"
            onClick={() => setOpen(true)}
          >
            注销账户
          </Button>
        }
      />
      <DeleteAccountDialog open={open} onOpenChange={setOpen} />
    </SettingsSection>
  );
}

/** Password-confirm modal for注销; on success drops the app back to login. */
function DeleteAccountDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await deleteAccount(password);
      // Account gone → drop to the login screen (AuthGate renders it on this).
      useAuthStore.getState().setUnauthenticated();
    } catch (e) {
      setError(errMsg(e, "注销失败，请重试"));
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setPassword("");
          setError(null);
        }
        onOpenChange(next);
      }}
      title="确认注销账户"
      description="此操作不可撤销。账户将被永久停用并匿名化，相关对话也会被删除。请输入密码以确认。"
      confirmLabel="确认注销"
      tone="danger"
      busy={busy}
      confirmDisabled={password.length === 0}
      onConfirm={() => void confirm()}
    >
      <Input
        className="w-full"
        type="password"
        value={password}
        aria-label="当前密码"
        placeholder="当前密码"
        autoComplete="current-password"
        onChange={(e) => {
          setError(null);
          setPassword(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && password && !busy) void confirm();
        }}
      />
      <SettingsFormMessage className="mt-2">{error}</SettingsFormMessage>
    </ConfirmDialog>
  );
}
