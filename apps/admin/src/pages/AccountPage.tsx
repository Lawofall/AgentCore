import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card, Page, PageHeader, SectionHeader } from "@/components/ui/Page";
import { Spinner } from "@/components/ui/Spinner";
import { errorMessageOr } from "@/services/api";
import { changePassword, updateProfile } from "@/services/auth";
import { useAuthStore } from "@/stores/auth";
import { type FormEvent, type ReactNode, useState } from "react";
import { toast } from "sonner";

/** One settings block: heading + a real `<form>`, so Enter submits it. */
function FormSection({
  title,
  description,
  onSubmit,
  children,
}: {
  title: string;
  description?: string;
  onSubmit: () => void;
  children: ReactNode;
}) {
  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    onSubmit();
  };
  return (
    <Card>
      <SectionHeader title={title} description={description} />
      <form
        onSubmit={handleSubmit}
        className="flex max-w-md flex-col gap-3 p-5"
      >
        {children}
      </form>
    </Card>
  );
}

function LabeledField({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-muted-foreground text-xs">{label}</span>
      {children}
    </label>
  );
}

function ProfileSection() {
  const user = useAuthStore((s) => s.user);
  const setAuthenticated = useAuthStore((s) => s.setAuthenticated);
  const [displayName, setDisplayName] = useState(user?.displayName ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmedName = displayName.trim();
  const trimmedEmail = email.trim();
  const dirty =
    trimmedName !== (user?.displayName ?? "") ||
    trimmedEmail !== (user?.email ?? "");
  const canSave = dirty && trimmedName.length > 0 && !saving;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await updateProfile({
        displayName: trimmedName,
        email: trimmedEmail || null,
      });
      setAuthenticated(updated);
      setDisplayName(updated.displayName);
      setEmail(updated.email ?? "");
      toast.success("资料已更新");
    } catch (e) {
      setError(errorMessageOr(e, "保存失败，请重试"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormSection
      title="个人资料"
      description="显示名会展示在侧栏。邮箱用于找回密码；未验证不影响登录。更改邮箱后需重新验证。"
      onSubmit={() => {
        if (canSave) void save();
      }}
    >
      <LabeledField label="用户名">
        <Input
          value={user?.username ?? ""}
          readOnly
          autoComplete="username"
          title="用户名不可修改"
          className="opacity-60"
        />
      </LabeledField>
      <LabeledField label="显示名">
        <Input
          value={displayName}
          maxLength={200}
          placeholder="你的显示名"
          autoComplete="nickname"
          onChange={(e) => setDisplayName(e.target.value)}
          disabled={saving}
        />
      </LabeledField>
      <LabeledField
        label={
          user?.emailVerifiedAt
            ? "邮箱 · 已验证"
            : user?.email
              ? "邮箱 · 未验证"
              : "邮箱 · 未填写"
        }
      >
        <Input
          type="email"
          value={email}
          maxLength={255}
          placeholder="you@example.com"
          autoComplete="email"
          onChange={(e) => setEmail(e.target.value)}
          disabled={saving}
        />
      </LabeledField>
      {error && (
        <p role="alert" className="text-destructive text-xs">
          {error}
        </p>
      )}
      <div className="flex justify-end">
        <Button type="submit" size="sm" disabled={!canSave}>
          {saving && <Spinner />}
          保存
        </Button>
      </div>
    </FormSection>
  );
}

function PasswordSection() {
  const username = useAuthStore((s) => s.user?.username) ?? "";
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

  const localError =
    next.length > 0 && next.length < 8
      ? "新密码至少需要 8 个字符"
      : confirm.length > 0 && next !== confirm
        ? "两次输入的新密码不一致"
        : null;
  const canSave =
    current.length > 0 && next.length >= 8 && next === confirm && !saving;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await changePassword(current, next);
      reset();
      toast.success("密码已更新，其他设备需重新登录");
    } catch (e) {
      setError(errorMessageOr(e, "修改失败，请重试"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormSection
      title="修改密码"
      description="修改后，除当前设备外的所有登录都会失效。"
      onSubmit={() => {
        if (canSave) void save();
      }}
    >
      {/*
        Password managers only offer to save a new password when the form also
        carries the account it belongs to; without this the change-password form
        is anonymous and the saved credential silently goes stale.
      */}
      <input
        type="text"
        name="username"
        value={username}
        readOnly
        tabIndex={-1}
        aria-hidden
        autoComplete="username"
        className="sr-only"
      />
      <LabeledField label="当前密码">
        <Input
          type="password"
          value={current}
          autoComplete="current-password"
          onChange={(e) => setCurrent(e.target.value)}
          disabled={saving}
        />
      </LabeledField>
      <LabeledField label="新密码（至少 8 位）">
        <Input
          type="password"
          value={next}
          minLength={8}
          autoComplete="new-password"
          onChange={(e) => setNext(e.target.value)}
          disabled={saving}
        />
      </LabeledField>
      <LabeledField label="确认新密码">
        <Input
          type="password"
          value={confirm}
          minLength={8}
          autoComplete="new-password"
          onChange={(e) => setConfirm(e.target.value)}
          disabled={saving}
        />
      </LabeledField>
      {(localError || error) && (
        <p role="alert" className="text-destructive text-xs">
          {localError ?? error}
        </p>
      )}
      <div className="flex justify-end">
        <Button type="submit" size="sm" disabled={!canSave}>
          {saving && <Spinner />}
          更新密码
        </Button>
      </div>
    </FormSection>
  );
}

export function AccountPage() {
  return (
    <Page>
      <PageHeader title="账户设置" description="管理你的个人资料与登录密码。" />
      <div className="flex max-w-3xl flex-col gap-6">
        <ProfileSection />
        <PasswordSection />
      </div>
    </Page>
  );
}
