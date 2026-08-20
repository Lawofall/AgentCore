import { IconButton } from "@/components/ui";
import { cn } from "@/lib/utils";
import { CheckCircle2, CircleAlert, Eye, EyeOff, Info } from "lucide-react";
import { useState } from "react";

export const inputClass =
  "h-10 w-full rounded-lg border border-input bg-background px-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring";

export const checkClass =
  "mt-0.5 size-4 shrink-0 rounded border border-input accent-primary";

export function FieldError({ children }: { children: string | null }) {
  if (!children) return null;
  return (
    <p className="text-xs text-muted-foreground" role="alert">
      {children}
    </p>
  );
}

export function AuthOutcome({
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
  return (
    <p
      role={kind === "error" ? "alert" : "status"}
      className={cn(
        "flex items-start gap-1.5 text-sm",
        kind === "success" ? "text-success" : "text-muted-foreground",
      )}
    >
      <Icon size={14} className="mt-0.5 shrink-0" aria-hidden />
      <span className="sr-only">{label}：</span>
      <span>{children}</span>
    </p>
  );
}

export function PasswordField({
  value,
  onChange,
  placeholder,
  autoComplete,
  disabled,
  invalid,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  autoComplete?: string;
  disabled?: boolean;
  invalid?: boolean;
}) {
  const [reveal, setReveal] = useState(false);
  return (
    <div className="relative">
      <input
        className={`${inputClass} pr-10`}
        type={reveal ? "text" : "password"}
        placeholder={placeholder}
        autoComplete={autoComplete}
        value={value}
        disabled={disabled}
        aria-invalid={invalid || undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      <IconButton
        type="button"
        aria-label={reveal ? "隐藏密码" : "显示密码"}
        className="absolute right-1 top-1/2 size-8 -translate-y-1/2"
        onClick={() => setReveal((v) => !v)}
      >
        {reveal ? <EyeOff size={16} /> : <Eye size={16} />}
      </IconButton>
    </div>
  );
}
