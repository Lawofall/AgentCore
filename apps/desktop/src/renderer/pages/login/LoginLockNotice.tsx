import { lockNoticeCopy } from "@/lib/loginLockout";
import { cn } from "@/lib/utils";
import { CheckCircle2, CircleAlert } from "lucide-react";

/**
 * Live lockout wait. The paragraph is not a live region while it ticks, so a
 * screen reader is not read the new second every beat. When the wait ends it
 * becomes a status and announces that login is open again.
 */
export function LoginLockNotice({
  left,
  hint,
}: {
  left: number;
  hint?: string;
}) {
  const done = left <= 0;
  const Icon = done ? CheckCircle2 : CircleAlert;
  return (
    <p
      role={done ? "status" : undefined}
      aria-live={done ? "polite" : "off"}
      className={cn(
        "flex items-start gap-1.5 text-sm",
        done ? "text-success" : "text-muted-foreground",
      )}
    >
      <Icon size={14} className="mt-0.5 shrink-0" aria-hidden />
      <span>{done ? "可以重新登录了" : lockNoticeCopy(left, hint)}</span>
    </p>
  );
}
