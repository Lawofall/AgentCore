import { IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { visibleMessageText } from "@/lib/errors";
import {
  NO_ACTIVE_MESSAGES,
  activeRuntime,
  useActiveUserTurnCount,
  useConversationStore,
} from "@/stores/conversation";
import { ListTree, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

/**
 * 对话大纲 / 回合导航 (对话基础功能补齐) — a floating table-of-contents over the *loaded*
 * turns of the open conversation. Each user message opens a turn, so the outline lists the
 * user prompts (whitespace-collapsed, truncated); clicking one jumps to that turn
 * (scroll) via the shared `focusMessage`. Complements 会话内查找 and 消息永久链接:
 * find by text, jump by structure.
 *
 * Only shown once there are ≥2 turns to navigate (a single-turn chat needs no outline).
 * Scope: loaded window only (same window 会话内查找 sees) — deep-history outline would need
 * paging and is intentionally out of scope for this control.
 */
export function ConversationOutline() {
  const userTurnCount = useActiveUserTurnCount();
  const focusMessage = useConversationStore((s) => s.focusMessage);
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const messages = useConversationStore((s) => {
    if (!open) return NO_ACTIVE_MESSAGES;
    return activeRuntime(s).messages;
  });

  const turns = useMemo(
    () =>
      messages
        .filter((m) => m.role === "user")
        .map((m) => ({
          id: m.id,
          label: visibleMessageText(m).replace(/\s+/g, " ").slice(0, 80),
        })),
    [messages],
  );

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (userTurnCount < 2) return null;

  const jump = (id: string) => {
    focusMessage(id, conversationId);
    setOpen(false);
  };

  return (
    <div ref={rootRef} className="absolute bottom-3 right-3 z-20">
      {open && (
        <div className="absolute bottom-11 right-0 max-h-[60vh] w-72 overflow-y-auto rounded-lg border border-border bg-card p-1 shadow-lg">
          <div className="flex items-center justify-between px-2 py-1.5">
            <span className="text-xs font-medium text-muted-foreground">
              对话大纲 · {turns.length} 个回合
            </span>
            <IconButton
              size="sm"
              aria-label="关闭大纲"
              onClick={() => setOpen(false)}
            >
              <X size={13} />
            </IconButton>
          </div>
          <ul className="space-y-0.5">
            {turns.map((t, i) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => jump(t.id)}
                  className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-sm text-foreground hover:bg-accent"
                >
                  <span className="mt-0.5 w-5 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                    {i + 1}
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    {t.label || "（空消息）"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <SimpleTooltip label="对话大纲 · 跳转回合">
        <IconButton
          size="md"
          aria-label="对话大纲"
          aria-pressed={open}
          onClick={() => setOpen((v) => !v)}
          className="rounded-full border border-border bg-card text-muted-foreground shadow-md hover:text-foreground"
        >
          <ListTree size={16} />
        </IconButton>
      </SimpleTooltip>
    </div>
  );
}
