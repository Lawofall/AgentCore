import { me } from "@/api/auth";
import {
  dismissDisplayNameHint,
  isDisplayNameHintDismissed,
} from "@/lib/displayNameHint";
import { isGeneratedHandle } from "@/lib/emailAuth";
import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

/** One-shot IM banner: generated `user_*` handles look odd as display names. */
export function DisplayNameHint() {
  const navigate = useNavigate();
  const [userId, setUserId] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    me()
      .then((u) => {
        if (cancelled) return;
        setUserId(u.id);
        setUsername(u.username);
        setDisplayName(u.display_name);
      })
      .catch(() => {
        /* list still works without the hint */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (
    !userId ||
    dismissed ||
    isDisplayNameHintDismissed(userId) ||
    !isGeneratedHandle(username, displayName)
  ) {
    return null;
  }

  return (
    // biome-ignore lint/a11y/useSemanticElements: 内嵌 CTA / 关闭按钮，<output> 语义不符——保留 aria live 容器。
    <div className="im-display-hint" role="status">
      <p>
        当前昵称是系统分配的找人码，在消息里看起来会比较怪。可到账户设置改成你希望别人看到的名字。
      </p>
      <button
        type="button"
        className="link"
        onClick={() => navigate("/more/account")}
      >
        去设置
      </button>
      <button
        type="button"
        className="im-display-hint-close"
        aria-label="关闭"
        onClick={() => {
          dismissDisplayNameHint(userId);
          setDismissed(true);
        }}
      >
        <X size={14} />
      </button>
    </div>
  );
}
