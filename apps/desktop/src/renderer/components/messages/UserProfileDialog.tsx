import { Button, IconButton } from "@/components/ui";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { copyText } from "@/lib/clipboard";
import { notifySuccess } from "@/lib/toast";
import {
  type UserProfile,
  acceptFriendRequest,
  blockUser,
  cancelFriendRequest,
  getUserProfile,
  messagingErrorMessage,
  rejectFriendRequest,
  removeFriend,
  sendFriendRequest,
  startDm,
} from "@/services/messaging";
import { useMessagingStore } from "@/stores/messaging";
import { Copy, Loader2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { PresenceAvatar } from "./PresenceAvatar";
import { avatarInitial } from "./chatDisplay";

interface Props {
  userId: string | null;
  open: boolean;
  onClose: () => void;
  /** After opening/reusing a DM — navigate to the thread. */
  onOpenChat: (chatId: string) => void;
}

/**
 * 资料卡 (消息IM.md §9.4): relation-driven actions — 加好友 / 已申请 / 同意·拒绝 /
 * 发消息 / 删好友 / 拉黑. Opened from group avatars, member rows, DM header, search.
 */
export function UserProfileDialog({
  userId,
  open,
  onClose,
  onOpenChat,
}: Props) {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [showRequestForm, setShowRequestForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmBlock, setConfirmBlock] = useState(false);
  const [confirmUnfriend, setConfirmUnfriend] = useState(false);
  // MessagesPage 单实例不重挂：切 userId / 开关卡时丢弃在途 getUserProfile。
  const genRef = useRef(0);

  const resetCardChrome = useCallback(() => {
    setError(null);
    setMessage("");
    setShowRequestForm(false);
    setConfirmBlock(false);
    setConfirmUnfriend(false);
    setBusy(false);
  }, []);

  const reload = useCallback(async (id: string) => {
    // 代次只跟 open/userId：同人重叠刷新 last-write-wins；切人由 cleanup bump。
    // 不能每请求 ++——run 在 reload 之后还要清 busy / 刷通讯录。
    const gen = genRef.current;
    setLoading(true);
    setError(null);
    try {
      const p = await getUserProfile(id);
      if (gen !== genRef.current) return;
      setProfile(p);
    } catch (err) {
      if (gen !== genRef.current) return;
      setProfile(null);
      setError(messagingErrorMessage(err, "无法加载资料"));
    } finally {
      if (gen === genRef.current) setLoading(false);
    }
  }, []);

  // userId / open 切换或卸载：丢弃在途结果（同 useGitRepoStatus）。
  // biome-ignore lint/correctness/useExhaustiveDependencies: deps 故意含 open/userId，切换时跑 cleanup bump gen
  useEffect(() => {
    return () => {
      genRef.current += 1;
    };
  }, [open, userId]);

  useEffect(() => {
    if (!open || !userId) {
      setProfile(null);
      resetCardChrome();
      return;
    }
    // 先清上一份，避免卡上仍是 A、操作闭包已是 B。
    setProfile(null);
    resetCardChrome();
    void reload(userId);
  }, [open, userId, reload, resetCardChrome]);

  // Firehose may change relation while the card is open — re-fetch on request events.
  useEffect(() => {
    if (!open || !userId) return;
    const unsub = useMessagingStore.subscribe((state, prev) => {
      if (
        state.friendRequestsIncoming !== prev.friendRequestsIncoming ||
        state.friendRequestsOutgoing !== prev.friendRequestsOutgoing ||
        state.friends !== prev.friends
      ) {
        void reload(userId);
      }
    });
    return unsub;
  }, [open, userId, reload]);

  const run = async (action: () => Promise<void>) => {
    // 只对「当前卡身份 = 列出这份资料的人」动手；切走了不改下一张卡。
    if (!userId || !profile || profile.id !== userId) return;
    const actingId = profile.id;
    const gen = genRef.current;
    setBusy(true);
    setError(null);
    try {
      await action();
      if (gen !== genRef.current) return;
      await reload(actingId);
      if (gen !== genRef.current) return;
      void useMessagingStore.getState().fetchFriendRequests();
      void useMessagingStore.getState().fetchFriends();
    } catch (err) {
      if (gen !== genRef.current) return;
      setError(messagingErrorMessage(err, "操作失败，请重试"));
    } finally {
      if (gen === genRef.current) {
        setBusy(false);
        setConfirmBlock(false);
        setConfirmUnfriend(false);
        setShowRequestForm(false);
        setMessage("");
      }
    }
  };

  const handleSendMessage = async () => {
    if (!profile || !userId || profile.id !== userId) return;
    const actingId = profile.id;
    const gen = genRef.current;
    setBusy(true);
    setError(null);
    try {
      const chat = await startDm(actingId);
      if (gen !== genRef.current) return;
      useMessagingStore.getState().upsertChat(chat);
      onOpenChat(chat.id);
      onClose();
    } catch (err) {
      if (gen !== genRef.current) return;
      setError(messagingErrorMessage(err, "无法发起私信"));
    } finally {
      if (gen === genRef.current) setBusy(false);
    }
  };

  const name = profile ? profile.display_name || profile.username : "用户资料";

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-sm" aria-describedby={undefined}>
        <div className="flex flex-col items-center gap-2 border-b border-border px-5 py-5">
          {loading && !profile ? (
            <Loader2
              size={28}
              className="animate-spin text-muted-foreground/50"
            />
          ) : (
            <PresenceAvatar
              label={avatarInitial(name)}
              url={profile?.avatar_url}
              sizeClass="size-14"
              textClass="text-xl"
              online={!!profile?.online}
            />
          )}
          <DialogTitle className="text-center">{name}</DialogTitle>
          {profile && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              @{profile.username}
              {profile.online ? " · 在线" : ""}
              <IconButton
                aria-label="复制用户名"
                className="size-6"
                onClick={() => {
                  void copyText(profile.username).then((ok) => {
                    if (ok) notifySuccess("已复制用户名");
                  });
                }}
              >
                <Copy size={12} />
              </IconButton>
            </span>
          )}
        </div>

        <div className="space-y-3 px-5 py-4">
          {error && (
            <p className="text-sm text-muted-foreground" role="alert">
              {error}
            </p>
          )}

          {profile?.relation === "self" && (
            <p className="text-center text-sm text-muted-foreground">
              这是你自己
            </p>
          )}

          {profile?.relation === "none" && !showRequestForm && (
            <Button
              className="w-full"
              disabled={busy}
              onClick={() => setShowRequestForm(true)}
            >
              加好友
            </Button>
          )}

          {profile?.relation === "none" && showRequestForm && (
            <div className="space-y-2">
              <textarea
                value={message}
                onChange={(e) => setMessage(e.target.value.slice(0, 100))}
                placeholder="验证语（可选）"
                rows={2}
                className="w-full resize-none rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                disabled={busy}
              />
              <div className="flex gap-2">
                <Button
                  variant="neutral"
                  className="flex-1"
                  disabled={busy}
                  onClick={() => {
                    setShowRequestForm(false);
                    setMessage("");
                  }}
                >
                  取消
                </Button>
                <Button
                  className="flex-1"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      if (!profile) return;
                      await sendFriendRequest(profile.id, message);
                    })
                  }
                >
                  发送申请
                </Button>
              </div>
            </div>
          )}

          {profile?.relation === "outgoing_request" && (
            <div className="space-y-2">
              <p className="text-center text-sm text-muted-foreground">
                已申请，等待对方处理
              </p>
              {profile.request_id && (
                <Button
                  variant="neutral"
                  className="w-full"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      if (!profile.request_id) return;
                      await cancelFriendRequest(profile.request_id);
                    })
                  }
                >
                  取消申请
                </Button>
              )}
            </div>
          )}

          {profile?.relation === "incoming_request" && profile.request_id && (
            <div className="flex gap-2">
              <Button
                variant="neutral"
                className="flex-1"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    if (!profile.request_id) return;
                    await rejectFriendRequest(profile.request_id);
                  })
                }
              >
                拒绝
              </Button>
              <Button
                className="flex-1"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    if (!profile.request_id) return;
                    await acceptFriendRequest(profile.request_id);
                  })
                }
              >
                同意
              </Button>
            </div>
          )}

          {profile?.relation === "friends" && (
            <div className="space-y-2">
              <Button
                className="w-full"
                disabled={busy}
                onClick={() => void handleSendMessage()}
              >
                发消息
              </Button>
              {confirmUnfriend ? (
                <div className="flex gap-2">
                  <Button
                    variant="neutral"
                    className="flex-1"
                    disabled={busy}
                    onClick={() => setConfirmUnfriend(false)}
                  >
                    取消
                  </Button>
                  <Button
                    variant="destructive"
                    className="flex-1"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        if (!profile) return;
                        await removeFriend(profile.id);
                      })
                    }
                  >
                    确认删除
                  </Button>
                </div>
              ) : (
                <Button
                  variant="ghost"
                  className="w-full text-muted-foreground"
                  disabled={busy}
                  onClick={() => setConfirmUnfriend(true)}
                >
                  删除好友
                </Button>
              )}
            </div>
          )}

          {profile?.relation === "blocked" && (
            <p className="text-center text-sm text-muted-foreground">
              已拉黑该用户
            </p>
          )}

          {profile &&
            profile.relation !== "self" &&
            profile.relation !== "blocked" && (
              <div className="border-t border-border pt-3">
                {confirmBlock ? (
                  <div className="flex gap-2">
                    <Button
                      variant="neutral"
                      className="flex-1"
                      disabled={busy}
                      onClick={() => setConfirmBlock(false)}
                    >
                      取消
                    </Button>
                    <Button
                      variant="destructive"
                      className="flex-1"
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          if (!profile) return;
                          await blockUser(profile.id);
                        })
                      }
                    >
                      确认拉黑
                    </Button>
                  </div>
                ) : (
                  <Button
                    variant="ghost"
                    className="w-full text-destructive"
                    disabled={busy}
                    onClick={() => setConfirmBlock(true)}
                  >
                    拉黑
                  </Button>
                )}
              </div>
            )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
