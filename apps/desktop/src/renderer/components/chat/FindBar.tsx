import { IconButton } from "@/components/ui";
import {
  NO_ACTIVE_MESSAGES,
  activeRuntime,
  useConversationStore,
} from "@/stores/conversation";
import { useUIStore } from "@/stores/ui";
import { ChevronDown, ChevronUp, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

/**
 * 会话内查找 (对话基础功能补齐) — a lightweight find bar over the *loaded* messages of the
 * open conversation. Matching is message-level (case-insensitive substring of the message
 * text); each hit scrolls to its bubble through the shared focus mechanism
 * (`focusMessage`, the same path search-hit "命中必达" uses). Ctrl/Cmd+F opens it,
 * Enter / Shift+Enter (or ↓/↑) walk the hits, Esc closes.
 *
 * Scope: only messages already in the window are searched — the same window every other
 * loaded-message affordance sees. Deep-history search across unloaded pages is a separate
 * (server-side) feature and intentionally out of scope for this bar.
 */
export function FindBar({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const messages = useConversationStore((s) => {
    if (!open) return NO_ACTIVE_MESSAGES;
    return activeRuntime(s).messages;
  });
  const focusMessage = useConversationStore((s) => s.focusMessage);
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const openSearch = useUIStore((s) => s.openSearch);
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [] as string[];
    return messages
      .filter((m) => m.content.toLowerCase().includes(q))
      .map((m) => m.id);
  }, [query, messages]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Land on the first hit whenever the *query* changes (not on every message tick —
  // keeping `messages` out of the deps means a live-streaming turn won't keep yanking
  // the view back to hit #1 while the bar is open). Recomputes the first match inline.
  // biome-ignore lint/correctness/useExhaustiveDependencies: re-run on query change only
  useEffect(() => {
    setIndex(0);
    const q = query.trim().toLowerCase();
    if (!q) return;
    const first = messages.find((m) => m.content.toLowerCase().includes(q));
    if (first) focusMessage(first.id, conversationId);
  }, [query]);

  if (!open) return null;

  const trimmed = query.trim();
  const empty = trimmed.length > 0 && matches.length === 0;

  const go = (delta: number) => {
    if (matches.length === 0) return;
    const next = (index + delta + matches.length) % matches.length;
    setIndex(next);
    focusMessage(matches[next], conversationId);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      go(e.shiftKey ? -1 : 1);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      go(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      go(-1);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  const openGlobalSearch = () => {
    const q = trimmed;
    onClose();
    openSearch(q);
  };

  return (
    <div className="absolute left-1/2 top-3 z-20 flex -translate-x-1/2 flex-col items-center gap-1">
      <div className="flex items-center gap-1 rounded-lg border border-border bg-card px-2 py-1.5 shadow-md">
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="在已加载消息中查找…"
          aria-label="在已加载消息中查找"
          className="min-w-[12rem] bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
        />
        <span
          className={`w-12 shrink-0 text-right text-xs tabular-nums ${
            empty ? "text-destructive" : "text-muted-foreground"
          }`}
        >
          {matches.length ? `${index + 1}/${matches.length}` : "0/0"}
        </span>
        <IconButton
          size="sm"
          aria-label="上一个匹配"
          disabled={matches.length === 0}
          onClick={() => go(-1)}
        >
          <ChevronUp size={14} />
        </IconButton>
        <IconButton
          size="sm"
          aria-label="下一个匹配"
          disabled={matches.length === 0}
          onClick={() => go(1)}
        >
          <ChevronDown size={14} />
        </IconButton>
        <IconButton size="sm" aria-label="关闭查找" onClick={onClose}>
          <X size={14} />
        </IconButton>
      </div>
      {empty && (
        <button
          type="button"
          onClick={openGlobalSearch}
          className="rounded-lg bg-card px-2.5 py-1 text-xs text-muted-foreground shadow-sm ring-1 ring-border hover:text-foreground"
        >
          未在已加载消息中找到 ·{" "}
          <span className="text-primary">在全对话中搜索</span>
        </button>
      )}
    </div>
  );
}
