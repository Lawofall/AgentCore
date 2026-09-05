import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Spinner";
import { errorMessage } from "@/services/api";
import { changePassword } from "@/services/auth";
import { applySession } from "@/services/session";
import { ShieldCheck } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";

/** Full-screen gate after an admin-reset temp password login. */
export function ForcePasswordChangePage() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const localError =
    next.length > 0 && next.length < 8
      ? "新密码至少需要 8 个字符"
      : confirm.length > 0 && next !== confirm
        ? "两次输入的新密码不一致"
        : null;
  const canSubmit =
    current.length > 0 && next.length >= 8 && next === confirm && !submitting;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await changePassword(current, next);
      // Re-derive the whole session rather than just marking it authenticated:
      // an account can be behind *both* gates (temp password + MFA enrollment),
      // and the store's default would drop the MFA one on the way through.
      await applySession();
      toast.success("密码已设置，可以正常使用管理后台");
    } catch (err) {
      setError(errorMessage(err));
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-xs">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <div className="flex size-12 items-center justify-center rounded-xl bg-warning/10 text-warning">
            <ShieldCheck size={24} />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-foreground">设置新密码</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              你正在使用临时密码登录，请先设置自己的新密码
            </p>
          </div>
        </div>

        <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-3">
          <Input
            type="password"
            autoComplete="current-password"
            placeholder="当前密码（临时密码）"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            disabled={submitting}
            autoFocus
          />
          <Input
            type="password"
            autoComplete="new-password"
            placeholder="新密码（至少 8 位）"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            disabled={submitting}
          />
          <Input
            type="password"
            autoComplete="new-password"
            placeholder="确认新密码"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            disabled={submitting}
          />
          {(localError || error) && (
            <p className="text-xs text-destructive">{localError ?? error}</p>
          )}
          <Button type="submit" disabled={!canSubmit} className="mt-1 w-full">
            {submitting && <Spinner />}
            {submitting ? "保存中…" : "确认并进入后台"}
          </Button>
        </form>
      </div>
    </div>
  );
}
