import { Button, Textarea } from "@/components/ui";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { notifyError, notifySuccess } from "@/lib/toast";
import {
  type ChatParticipant,
  messagingErrorMessage,
} from "@/services/messaging";
import { useAuthStore } from "@/stores/auth";
import { useChatMembers, useMessagingStore } from "@/stores/messaging";
import { LogOut, Megaphone } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { GovernanceBadge } from "./GovernanceBadge";
import { PresenceAvatar } from "./PresenceAvatar";
import {
  avatarInitial,
  canActAsGroupModerator,
  canModerateMemberTarget,
  chatCircleAvatarUrl,
  chatDisplayName,
  memberGovernanceBadge,
} from "./chatDisplay";

interface Props {
  chatId: string;
  open: boolean;
  onClose: () => void;
}

/** A pill switch for a per-chat flag (mute / pin). */
function Toggle({
  on,
  onToggle,
  label,
}: {
  on: boolean;
  onToggle: () => void;
  label: string;
}) {
  return (
    <Button
      variant="ghost"
      role="switch"
      aria-checked={on}
      onClick={onToggle}
      className="h-auto w-full justify-between px-1 py-2 text-sm hover:bg-accent/50"
    >
      <span className="text-foreground">{label}</span>
      <span
        className={`flex h-5 w-9 items-center rounded-full px-0.5 transition-colors ${
          on ? "bg-primary" : "bg-muted"
        }`}
      >
        <span
          className={`size-4 rounded-full bg-background transition-transform ${
            on ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </span>
    </Button>
  );
}

/**
 * 群信息 / 官方号会话设置: per-chat mute & pin. Groups also show roster + leave;
 * moderators see kick / mute / announce. The official broadcast chat omits leave
 * (backend 422) and the member list.
 */
export function GroupInfoDialog({ chatId, open, onClose }: Props) {
  const chat = useMessagingStore(
    (s) => s.chats.find((c) => c.id === chatId) ?? null,
  );
  const members = useChatMembers(chatId);
  const loadMembers = useMessagingStore((s) => s.loadMembers);
  const setMembershipFlags = useMessagingStore((s) => s.setMembershipFlags);
  const leaveChat = useMessagingStore((s) => s.leaveChat);
  const kickMember = useMessagingStore((s) => s.kickMember);
  const setAdminMute = useMessagingStore((s) => s.setAdminMute);
  const announce = useMessagingStore((s) => s.announce);
  const openProfile = useMessagingStore((s) => s.openProfile);
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();
  const [confirmingLeave, setConfirmingLeave] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [kickConfirmId, setKickConfirmId] = useState<string | null>(null);
  const [busyUserId, setBusyUserId] = useState<string | null>(null);
  const [announceDraft, setAnnounceDraft] = useState("");
  const [announcing, setAnnouncing] = useState(false);
  const [showAnnounce, setShowAnnounce] = useState(false);

  const myId = user?.id ?? null;
  const isPlatformAdmin = user?.role === "admin";
  const myMembership = useMemo(
    () => (myId ? members.find((m) => m.id === myId) : undefined),
    [members, myId],
  );
  const canGovern = canActAsGroupModerator(
    isPlatformAdmin,
    myMembership?.group_role,
  );

  useEffect(() => {
    if (open) {
      setConfirmingLeave(false);
      setKickConfirmId(null);
      setAnnounceDraft("");
      setShowAnnounce(false);
      if (chat?.type !== "official") void loadMembers(chatId);
    }
  }, [open, chatId, loadMembers, chat?.type]);

  if (!chat) return null;
  const name = chatDisplayName(chat);
  const isOfficial = chat.type === "official";
  const isGroup = chat.type === "group";

  const handleLeave = async () => {
    setLeaving(true);
    const ok = await leaveChat(chatId);
    setLeaving(false);
    if (ok) {
      onClose();
      navigate("/messages");
    }
  };

  const runMemberAction = async (
    targetId: string,
    action: () => Promise<void>,
  ) => {
    setBusyUserId(targetId);
    try {
      await action();
      setKickConfirmId(null);
    } catch (err) {
      notifyError(err, messagingErrorMessage(err, "操作失败，请重试"));
    } finally {
      setBusyUserId(null);
    }
  };

  const handleAnnounce = async () => {
    const content = announceDraft.trim();
    if (!content) return;
    setAnnouncing(true);
    try {
      await announce(chatId, content);
      notifySuccess("公告已发送");
      setAnnounceDraft("");
      setShowAnnounce(false);
    } catch (err) {
      notifyError(err, messagingErrorMessage(err, "发布公告失败"));
    } finally {
      setAnnouncing(false);
    }
  };

  const renderMemberActions = (m: ChatParticipant) => {
    if (!isGroup || !canGovern) return null;
    if (
      !canModerateMemberTarget({
        myUserId: myId,
        isPlatformAdmin,
        target: m,
      })
    ) {
      return null;
    }
    const busy = busyUserId === m.id;
    if (kickConfirmId === m.id) {
      return (
        <div className="mt-1 flex flex-wrap items-center gap-2 pl-11">
          <span className="text-xs text-muted-foreground">确认踢出？</span>
          <Button
            variant="neutral"
            className="h-7 px-2 text-xs"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              setKickConfirmId(null);
            }}
          >
            取消
          </Button>
          <Button
            variant="destructive"
            className="h-7 px-2 text-xs"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              void runMemberAction(m.id, () => kickMember(chatId, m.id));
            }}
          >
            {busy ? "处理中…" : "确认踢出"}
          </Button>
        </div>
      );
    }
    return (
      <div className="mt-1 flex flex-wrap gap-2 pl-11">
        <Button
          variant="ghost"
          className="h-7 px-2 text-xs text-muted-foreground"
          disabled={busy}
          onClick={(e) => {
            e.stopPropagation();
            void runMemberAction(m.id, () =>
              setAdminMute(chatId, m.id, !m.muted_by_admin),
            );
          }}
        >
          {m.muted_by_admin ? "解禁" : "禁言"}
        </Button>
        <Button
          variant="ghost"
          className="h-7 px-2 text-xs text-destructive"
          disabled={busy}
          onClick={(e) => {
            e.stopPropagation();
            setKickConfirmId(m.id);
          }}
        >
          踢出
        </Button>
      </div>
    );
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-sm" aria-describedby={undefined}>
        <div className="flex flex-col items-center gap-2 border-b border-border px-5 py-5">
          <PresenceAvatar
            label={avatarInitial(name)}
            url={chatCircleAvatarUrl(chat)}
            sizeClass="size-14"
            textClass="text-xl"
          />
          <DialogTitle className="text-center">{name}</DialogTitle>
          <span className="text-xs text-muted-foreground">
            {isOfficial ? "官方广播" : `${members.length} 名成员`}
          </span>
        </div>

        <div className="px-4 py-2">
          <Toggle
            label="消息免打扰"
            on={chat.muted}
            onToggle={() =>
              void setMembershipFlags(chatId, { muted: !chat.muted })
            }
          />
          <Toggle
            label="置顶会话"
            on={chat.pinned}
            onToggle={() =>
              void setMembershipFlags(chatId, { pinned: !chat.pinned })
            }
          />
        </div>

        {isGroup && canGovern && (
          <div className="border-t border-border px-4 py-3">
            {showAnnounce ? (
              <div className="space-y-2">
                <Textarea
                  value={announceDraft}
                  onChange={(e) => setAnnounceDraft(e.target.value)}
                  placeholder="输入群公告内容"
                  rows={3}
                  className="text-sm"
                  disabled={announcing}
                />
                <div className="flex justify-end gap-2">
                  <Button
                    variant="neutral"
                    className="h-8"
                    disabled={announcing}
                    onClick={() => {
                      setShowAnnounce(false);
                      setAnnounceDraft("");
                    }}
                  >
                    取消
                  </Button>
                  <Button
                    className="h-8"
                    disabled={announcing || !announceDraft.trim()}
                    onClick={() => void handleAnnounce()}
                  >
                    {announcing ? "发送中…" : "发布"}
                  </Button>
                </div>
              </div>
            ) : (
              <Button
                variant="ghost"
                className="h-auto w-full justify-start gap-2 px-1 py-2 text-sm"
                icon={<Megaphone size={16} />}
                onClick={() => setShowAnnounce(true)}
              >
                发群公告
              </Button>
            )}
          </div>
        )}

        {!isOfficial && (
          <div className="min-h-0 border-t border-border">
            <p className="px-5 pb-1 pt-3 text-xs font-medium text-muted-foreground">
              成员
            </p>
            <ul className="max-h-60 overflow-y-auto px-2 pb-2">
              {members.map((m) => {
                const badge = memberGovernanceBadge(m);
                return (
                  <li key={m.id} className="py-0.5">
                    <button
                      type="button"
                      onClick={() => openProfile(m.id)}
                      className="flex w-full items-center gap-3 rounded-lg px-3 py-1.5 text-left hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      aria-label={`查看 ${m.display_name || m.username} 的资料`}
                    >
                      <PresenceAvatar
                        label={avatarInitial(m.display_name || m.username)}
                        url={m.avatar_url}
                        sizeClass="size-8"
                        textClass="text-sm"
                        online={!!m.online}
                      />
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                          <span className="truncate text-sm text-foreground">
                            {m.display_name || m.username}
                          </span>
                          {badge && <GovernanceBadge badge={badge} />}
                          {m.muted_by_admin && (
                            <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                              已禁言
                            </span>
                          )}
                        </span>
                        <span className="truncate text-xs text-muted-foreground">
                          {m.online ? "在线" : `@${m.username}`}
                        </span>
                      </span>
                    </button>
                    {renderMemberActions(m)}
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {!isOfficial && (
          <div className="border-t border-border px-5 py-4">
            {confirmingLeave ? (
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm text-muted-foreground">
                  退出后需重新邀请才能再加入
                </span>
                <div className="flex shrink-0 gap-2">
                  <Button
                    variant="neutral"
                    onClick={() => setConfirmingLeave(false)}
                  >
                    取消
                  </Button>
                  <Button
                    variant="destructive"
                    className="disabled:opacity-50"
                    disabled={leaving}
                    onClick={() => void handleLeave()}
                  >
                    {leaving ? "退出中…" : "确认退出"}
                  </Button>
                </div>
              </div>
            ) : (
              <Button
                variant="danger"
                className="h-auto w-full py-2 text-sm"
                icon={<LogOut size={16} />}
                onClick={() => setConfirmingLeave(true)}
              >
                退出群聊
              </Button>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
