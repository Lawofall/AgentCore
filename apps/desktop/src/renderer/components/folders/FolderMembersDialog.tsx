import { EmptyHint, InlineError } from "@/components/files/parts";
import { avatarInitial } from "@/components/messages/chatDisplay";
import { Badge, Button, SearchField } from "@/components/ui";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useChangeFolderMemberRole,
  useFolderMembers,
  useInviteFolderMember,
  useRemoveOrLeaveFolderMember,
} from "@/hooks/useFolderSharing";
import { notifyError } from "@/lib/toast";
import {
  type FolderInviteRole,
  type FolderMemberRole,
  type FolderMemberState,
  folderRoleLabel,
} from "@/services/folders";
import {
  type FriendSummary,
  type UserSearchResult,
  messagingErrorMessage,
  searchUsers,
} from "@/services/messaging";
import { useAuthStore } from "@/stores/auth";
import { useMessagingStore } from "@/stores/messaging";
import { Check, Loader2, UserPlus, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

function roleTone(role: FolderMemberRole): "primary" | "success" | "muted" {
  if (role === "owner") return "primary";
  if (role === "editor") return "success";
  return "muted";
}

function membershipLabel(state: FolderMemberState): string {
  return state === "pending" ? "待接受" : "已加入";
}

/**
 * Owner: friend multi-select invite + exact search / change role / remove
 * (pending →「取消邀请」). Member: view roster + leave self. Cloud folders only.
 */
export function FolderMembersDialog({
  open,
  onClose,
  folderId,
  folderName,
  myRole,
}: {
  open: boolean;
  onClose: () => void;
  folderId: string;
  folderName: string;
  myRole: FolderMemberRole;
}) {
  const meId = useAuthStore((s) => s.user?.id ?? null);
  const isOwner = myRole === "owner";
  const { data, isLoading, isError, refetch } = useFolderMembers(
    open ? folderId : null,
  );
  const members = data ?? [];
  const invite = useInviteFolderMember();
  const changeRole = useChangeFolderMemberRole();
  const removeOrLeave = useRemoveOrLeaveFolderMember();

  const friends = useMessagingStore((s) => s.friends);
  const friendsLoaded = useMessagingStore((s) => s.friendsLoaded);
  const fetchFriends = useMessagingStore((s) => s.fetchFriends);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<UserSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [inviteRole, setInviteRole] = useState<FolderInviteRole>("editor");
  const [selectedFriendIds, setSelectedFriendIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [batchInviting, setBatchInviting] = useState(false);

  const memberStateByUserId = useMemo(() => {
    const map = new Map<string, FolderMemberState>();
    for (const m of members) map.set(m.user_id, m.state);
    return map;
  }, [members]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setResults([]);
    setSearchError(null);
    setInviteRole("editor");
    setSelectedFriendIds(new Set());
    setBatchInviting(false);
  }, [open]);

  useEffect(() => {
    if (!open || !isOwner) return;
    void fetchFriends();
  }, [open, isOwner, fetchFriends]);

  useEffect(() => {
    setSelectedFriendIds((prev) => {
      if (prev.size === 0) return prev;
      let changed = false;
      const next = new Set<string>();
      for (const id of prev) {
        if (memberStateByUserId.has(id)) {
          changed = true;
          continue;
        }
        next.add(id);
      }
      return changed ? next : prev;
    });
  }, [memberStateByUserId]);

  useEffect(() => {
    if (!open || !isOwner) return;
    const q = query.trim();
    if (!q) {
      setResults([]);
      setSearchError(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const users = await searchUsers(q);
          if (!cancelled) {
            setResults(users);
            setSearchError(null);
          }
        } catch (err) {
          if (!cancelled) {
            setResults([]);
            setSearchError(messagingErrorMessage(err, "搜索失败，请重试"));
          }
        } finally {
          if (!cancelled) setSearching(false);
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query, open, isOwner]);

  const toggleFriend = (friend: FriendSummary) => {
    if (memberStateByUserId.has(friend.id)) return;
    setSelectedFriendIds((prev) => {
      const next = new Set(prev);
      if (next.has(friend.id)) next.delete(friend.id);
      else next.add(friend.id);
      return next;
    });
  };

  const handleInviteOne = (user: {
    id: string;
    display_name: string;
    username: string;
  }) => {
    if (memberStateByUserId.has(user.id)) return;
    invite.mutate(
      { folderId, userId: user.id, role: inviteRole },
      {
        onSuccess: () => {
          setQuery("");
          setResults([]);
          setSelectedFriendIds((prev) => {
            if (!prev.has(user.id)) return prev;
            const next = new Set(prev);
            next.delete(user.id);
            return next;
          });
        },
        onError: (err) => notifyError(err, "邀请失败"),
      },
    );
  };

  const handleInviteSelected = async () => {
    const ids = [...selectedFriendIds].filter(
      (id) => !memberStateByUserId.has(id),
    );
    if (ids.length === 0) return;
    setBatchInviting(true);
    let failed = 0;
    for (const userId of ids) {
      try {
        await invite.mutateAsync({ folderId, userId, role: inviteRole });
      } catch {
        failed += 1;
      }
    }
    setBatchInviting(false);
    setSelectedFriendIds(new Set());
    if (failed > 0) notifyError(`有 ${failed} 人邀请失败`);
  };

  const inviteBusy = invite.isPending || batchInviting;
  const selectedCount = selectedFriendIds.size;

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        position="top"
        className="max-w-md"
        aria-describedby={undefined}
      >
        <DialogHeader>
          <DialogTitle>成员 · {folderName}</DialogTitle>
        </DialogHeader>

        {isOwner && (
          <div className="space-y-2 border-b border-border px-5 pb-3">
            <div className="flex items-center gap-2">
              <UserPlus size={14} className="shrink-0 text-muted-foreground" />
              <span className="text-xs font-medium text-muted-foreground">
                邀请成员
              </span>
              <div className="ml-auto flex gap-1">
                {(
                  [
                    ["editor", "可编辑"],
                    ["viewer", "只读"],
                  ] as const
                ).map(([value, label]) => (
                  <Button
                    key={value}
                    size="sm"
                    variant={inviteRole === value ? "primary" : "ghost"}
                    onClick={() => setInviteRole(value)}
                  >
                    {label}
                  </Button>
                ))}
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              可从好友选择，或精确搜索用户名 / ID。
            </p>

            <div className="space-y-1.5">
              <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <Users size={12} aria-hidden />
                好友
              </p>
              {!friendsLoaded && friends.length === 0 ? (
                <p className="py-3 text-center text-xs text-muted-foreground">
                  加载中…
                </p>
              ) : friends.length === 0 ? (
                <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
                  暂无好友。可精确搜索用户名或 ID 邀请。
                </p>
              ) : (
                <ul className="max-h-40 overflow-y-auto rounded-lg border border-border">
                  {friends.map((f) => {
                    const membership = memberStateByUserId.get(f.id);
                    const disabled = !!membership || inviteBusy;
                    const selected = selectedFriendIds.has(f.id);
                    const label = f.display_name || f.username;
                    return (
                      <li key={f.id}>
                        <Button
                          variant="ghost"
                          disabled={disabled}
                          aria-pressed={selected}
                          onClick={() => toggleFriend(f)}
                          className="h-auto w-full justify-start gap-2 rounded-none px-3 py-2 font-normal disabled:opacity-60"
                        >
                          <span
                            className={`flex size-4 shrink-0 items-center justify-center rounded border ${
                              selected
                                ? "border-primary bg-primary text-primary-foreground"
                                : "border-border bg-background"
                            } ${membership ? "opacity-40" : ""}`}
                            aria-hidden
                          >
                            {selected ? (
                              <Check size={10} strokeWidth={3} />
                            ) : null}
                          </span>
                          <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-medium text-primary">
                            {avatarInitial(label)}
                          </span>
                          <span className="min-w-0 flex-1 truncate text-left text-sm">
                            {label}
                            <span className="ml-1 text-xs text-muted-foreground">
                              @{f.username}
                            </span>
                          </span>
                          {membership ? (
                            <span className="shrink-0 text-xs text-muted-foreground">
                              {membershipLabel(membership)}
                            </span>
                          ) : null}
                        </Button>
                      </li>
                    );
                  })}
                </ul>
              )}
              {selectedCount > 0 && (
                <div className="flex items-center justify-between gap-2 pt-0.5">
                  <span className="text-xs text-muted-foreground">
                    已选 {selectedCount} 人
                  </span>
                  <Button
                    size="sm"
                    variant="primary"
                    disabled={inviteBusy}
                    onClick={() => void handleInviteSelected()}
                  >
                    {batchInviting ? "邀请中…" : `邀请 ${selectedCount} 人`}
                  </Button>
                </div>
              )}
            </div>

            <SearchField
              variant="plain"
              value={query}
              onValueChange={setQuery}
              placeholder="精确查找用户名或 ID…"
              aria-label="按用户名或 ID 邀请成员"
              className="rounded-lg border border-border px-2"
            />
            {searchError && (
              <p className="text-xs text-muted-foreground">{searchError}</p>
            )}
            {searching && (
              <p className="text-xs text-muted-foreground">查找中…</p>
            )}
            {!searching &&
              query.trim() &&
              results.length === 0 &&
              !searchError && (
                <p className="text-xs text-muted-foreground">
                  未找到用户（需精确用户名或 ID）
                </p>
              )}
            {results.length > 0 && (
              <ul className="max-h-40 overflow-y-auto rounded-lg border border-border">
                {results.map((u) => {
                  const membership = memberStateByUserId.get(u.id);
                  const disabled = !!membership || inviteBusy;
                  return (
                    <li key={u.id}>
                      <Button
                        variant="ghost"
                        disabled={disabled}
                        onClick={() => handleInviteOne(u)}
                        className="h-auto w-full justify-start gap-2 rounded-none px-3 py-2 font-normal disabled:opacity-60"
                      >
                        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-medium text-primary">
                          {avatarInitial(u.display_name || u.username)}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-left text-sm">
                          {u.display_name || u.username}
                          <span className="ml-1 text-xs text-muted-foreground">
                            @{u.username}
                          </span>
                        </span>
                        {membership ? (
                          <span className="shrink-0 text-xs text-muted-foreground">
                            {membershipLabel(membership)}
                          </span>
                        ) : (
                          <span className="shrink-0 text-xs text-muted-foreground">
                            邀请
                          </span>
                        )}
                      </Button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}

        <div className="max-h-[50vh] min-h-[8rem] overflow-y-auto px-5 pb-5">
          {isLoading ? (
            <div className="flex items-center justify-center py-10">
              <Loader2
                size={18}
                className="animate-spin text-muted-foreground/50"
              />
            </div>
          ) : isError ? (
            <InlineError onRetry={() => void refetch()} />
          ) : members.length === 0 ? (
            <EmptyHint
              inline
              icon={<Users size={22} className="text-muted-foreground/40" />}
              title="暂无成员"
              hint="邀请同伴加入后，他们会出现在这里。"
            />
          ) : (
            <ul className="divide-y divide-border">
              {members.map((m) => {
                const isSelf = m.user_id === meId;
                const label =
                  m.display_name || m.username || m.user_id.slice(0, 8);
                const busy =
                  (changeRole.isPending &&
                    changeRole.variables?.memberUserId === m.user_id) ||
                  (removeOrLeave.isPending &&
                    removeOrLeave.variables?.memberUserId === m.user_id);
                const isPending = m.state === "pending";
                return (
                  <li
                    key={m.user_id}
                    className="flex items-center gap-2 py-2.5"
                  >
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-medium text-primary">
                      {avatarInitial(label)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">
                        {label}
                        {isSelf ? (
                          <span className="ml-1 text-xs text-muted-foreground">
                            （你）
                          </span>
                        ) : null}
                      </p>
                      <div className="mt-0.5 flex flex-wrap items-center gap-1">
                        <Badge tone={roleTone(m.role)} pill>
                          {folderRoleLabel(m.role)}
                        </Badge>
                        {isPending && (
                          <Badge tone="muted" pill>
                            待接受
                          </Badge>
                        )}
                      </div>
                    </div>
                    {isOwner &&
                      m.role !== "owner" &&
                      m.state === "accepted" && (
                        <select
                          className="h-7 max-w-[5.5rem] rounded-lg border border-border bg-background px-1.5 text-xs"
                          value={m.role}
                          disabled={busy}
                          aria-label={`更改 ${label} 的角色`}
                          onChange={(e) => {
                            const role = e.target.value as FolderInviteRole;
                            changeRole.mutate(
                              {
                                folderId,
                                memberUserId: m.user_id,
                                role,
                              },
                              {
                                onError: (err) =>
                                  notifyError(err, "更改角色失败"),
                              },
                            );
                          }}
                        >
                          <option value="editor">可编辑</option>
                          <option value="viewer">只读</option>
                        </select>
                      )}
                    {isOwner && m.role !== "owner" && (
                      <Button
                        size="sm"
                        variant="danger"
                        disabled={busy}
                        onClick={() => {
                          if (isPending) {
                            if (!window.confirm(`取消对「${label}」的邀请？`)) {
                              return;
                            }
                          } else if (
                            !window.confirm(`确定移除成员「${label}」？`)
                          ) {
                            return;
                          }
                          removeOrLeave.mutate(
                            { folderId, memberUserId: m.user_id },
                            {
                              onError: (err) =>
                                notifyError(
                                  err,
                                  isPending ? "取消邀请失败" : "移除成员失败",
                                ),
                            },
                          );
                        }}
                      >
                        {isPending ? "取消邀请" : "移除"}
                      </Button>
                    )}
                    {!isOwner && isSelf && (
                      <Button
                        size="sm"
                        variant="danger"
                        disabled={busy}
                        onClick={() => {
                          if (!window.confirm(`确定退出「${folderName}」？`)) {
                            return;
                          }
                          removeOrLeave.mutate(
                            { folderId, memberUserId: m.user_id },
                            {
                              onSuccess: () => {
                                onClose();
                              },
                              onError: (err) => notifyError(err, "退出失败"),
                            },
                          );
                        }}
                      >
                        退出
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
