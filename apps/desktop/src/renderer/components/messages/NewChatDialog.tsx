import { Button, IconButton, SearchField } from "@/components/ui";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import {
  type UserSearchResult,
  messagingErrorMessage,
  searchUsers,
} from "@/services/messaging";
import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { PresenceAvatar } from "./PresenceAvatar";
import { avatarInitial } from "./chatDisplay";

interface Props {
  open: boolean;
  onClose: () => void;
  /** Open the profile card — never jump straight into a free DM (§9.4). */
  onOpenProfile: (userId: string) => void;
}

/**
 * Exact-match people search. Results open the 资料卡 (加好友 / 发消息 by
 * relation); they no longer auto-start a DM as the only path (消息IM.md §9.4).
 */
export function NewChatDialog({ open, onClose, onOpenProfile }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<UserSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setResults([]);
    setError(null);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    if (!q) {
      setResults([]);
      setError(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const users = await searchUsers(q);
          if (!cancelled) {
            setResults(users);
            setError(null);
          }
        } catch (err) {
          if (!cancelled) {
            setResults([]);
            setError(messagingErrorMessage(err, "搜索失败，请重试"));
          }
        } finally {
          if (!cancelled) setLoading(false);
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query, open]);

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        position="top"
        showClose={false}
        size="md"
        aria-describedby={undefined}
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          inputRef.current?.focus();
        }}
      >
        <DialogTitle className="sr-only">查找用户</DialogTitle>
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <SearchField
            ref={inputRef}
            variant="plain"
            value={query}
            onValueChange={setQuery}
            placeholder="按用户名或 ID 查找…"
            aria-label="按用户名或 ID 查找用户"
            clearable={false}
            escapeClears={false}
            className="flex-1"
          />
          <IconButton
            onClick={onClose}
            aria-label="关闭"
            className="shrink-0 hover:bg-transparent"
          >
            <X size={15} />
          </IconButton>
        </div>

        <div className="max-h-80 overflow-y-auto">
          {error && (
            <p className="px-4 py-3 text-sm text-muted-foreground">{error}</p>
          )}
          {!error && loading && (
            <p className="px-4 py-6 text-center text-sm text-muted-foreground">
              查找中…
            </p>
          )}
          {!error && !loading && !query.trim() && (
            <p className="px-4 py-6 text-center text-sm text-muted-foreground">
              输入用户名或 ID 查找用户，结果将打开资料卡
            </p>
          )}
          {!error && !loading && query.trim() && results.length === 0 && (
            <p className="px-4 py-6 text-center text-sm text-muted-foreground">
              未找到用户（需精确用户名或 ID）
            </p>
          )}
          {results.length > 0 && (
            <ul className="py-1.5">
              {results.map((u) => (
                <li key={u.id}>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      onOpenProfile(u.id);
                      onClose();
                    }}
                    className="h-auto w-full justify-start gap-3 rounded-none px-4 py-2 font-normal"
                  >
                    <span className="flex w-full items-center gap-3 text-left">
                      <PresenceAvatar
                        label={avatarInitial(u.display_name || u.username)}
                        url={u.avatar_url}
                        sizeClass="size-8"
                        textClass="text-sm"
                      />
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="truncate text-sm text-foreground">
                          {u.display_name || u.username}
                        </span>
                        <span className="truncate text-xs text-muted-foreground">
                          @{u.username}
                        </span>
                      </span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        查看资料
                      </span>
                    </span>
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
