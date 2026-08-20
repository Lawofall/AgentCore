import { IconButton } from "@/components/ui";
import {
  dismissDisplayNameHint,
  isDisplayNameHintDismissed,
} from "@/lib/displayNameHint";
import { isGeneratedHandle } from "@/lib/emailAuth";
import { useAuthStore } from "@/stores/auth";
import { X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

/** One-shot IM banner: generated `user_*` handles look odd as nicknames. */
export function DisplayNameHint() {
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const [dismissed, setDismissed] = useState(false);

  if (
    !user ||
    dismissed ||
    isDisplayNameHintDismissed(user.id) ||
    !isGeneratedHandle(user.username, user.displayName)
  ) {
    return null;
  }

  return (
    // biome-ignore lint/a11y/useSemanticElements: 内嵌 CTA / 关闭按钮，<output> 语义不符——保留 aria live 容器。
    <div
      role="status"
      className="mx-3 mb-2 flex items-start gap-2 rounded-lg border border-border bg-muted/60 px-3 py-2 text-xs text-muted-foreground"
    >
      <p className="min-w-0 flex-1 leading-relaxed">
        当前昵称是系统分配的找人码，在消息里看起来会比较怪。可到账户设置改成你希望别人看到的名字。
      </p>
      <button
        type="button"
        className="shrink-0 text-foreground underline-offset-2 hover:underline"
        onClick={() => navigate("/more/account")}
      >
        去设置
      </button>
      <IconButton
        aria-label="关闭"
        className="size-6 shrink-0"
        onClick={() => {
          dismissDisplayNameHint(user.id);
          setDismissed(true);
        }}
      >
        <X size={12} />
      </IconButton>
    </div>
  );
}
