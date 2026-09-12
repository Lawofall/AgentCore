import { Button } from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { notifyError } from "@/lib/toast";
import {
  acceptFriendRequest,
  messagingErrorMessage,
  rejectFriendRequest,
} from "@/services/messaging";
import { useMessagingStore } from "@/stores/messaging";
import { useEffect, useState } from "react";
import { PresenceAvatar } from "./PresenceAvatar";
import { avatarInitial } from "./chatDisplay";

interface Props {
  open: boolean;
  onClose: () => void;
  onOpenProfile: (userId: string) => void;
}

/**
 * 「新的朋友」申请箱：incoming 同意/拒绝；outgoing 只读展示。
 *
 * While open, re-pull on focus (and a light interval) so a peer accept is not
 * stuck as「等待对方处理」when firehose delivery is missed (多 worker ⏳).
 */
export function FriendRequestsDialog({ open, onClose, onOpenProfile }: Props) {
  const incoming = useMessagingStore((s) => s.friendRequestsIncoming);
  const outgoing = useMessagingStore((s) => s.friendRequestsOutgoing);
  const fetchFriendRequests = useMessagingStore((s) => s.fetchFriendRequests);
  const fetchFriends = useMessagingStore((s) => s.fetchFriends);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const refresh = () => {
      void (async () => {
        await fetchFriends();
        await fetchFriendRequests();
      })();
    };
    refresh();
    window.addEventListener("focus", refresh);
    const interval = window.setInterval(refresh, 8_000);
    return () => {
      window.removeEventListener("focus", refresh);
      window.clearInterval(interval);
    };
  }, [open, fetchFriendRequests, fetchFriends]);

  const handleAccept = async (id: string) => {
    setBusyId(id);
    try {
      await acceptFriendRequest(id);
      await fetchFriends();
      await fetchFriendRequests();
    } catch (err) {
      notifyError(err, messagingErrorMessage(err, "同意失败"));
    } finally {
      setBusyId(null);
    }
  };

  const handleReject = async (id: string) => {
    setBusyId(id);
    try {
      await rejectFriendRequest(id);
      await fetchFriendRequests();
    } catch (err) {
      notifyError(err, messagingErrorMessage(err, "拒绝失败"));
    } finally {
      setBusyId(null);
    }
  };

  const pendingIncoming = incoming.filter(
    (r) => r.status == null || r.status === "pending",
  );
  const pendingOutgoing = outgoing.filter(
    (r) => r.status == null || r.status === "pending",
  );

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent size="md" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>新的朋友</DialogTitle>
        </DialogHeader>

        <DialogBody className="max-h-96 px-0">
          <p className="px-5 pb-1 pt-3 text-xs font-medium text-muted-foreground">
            收到的申请
          </p>
          {pendingIncoming.length === 0 ? (
            <p className="px-5 py-4 text-sm text-muted-foreground">暂无申请</p>
          ) : (
            <ul className="pb-2">
              {pendingIncoming.map((r) => {
                const u = r.peer;
                const label =
                  u?.display_name || u?.username || r.from_user_id.slice(0, 8);
                return (
                  <li key={r.id} className="flex items-center gap-3 px-5 py-2">
                    <button
                      type="button"
                      onClick={() => onOpenProfile(r.from_user_id)}
                      className="flex min-w-0 flex-1 items-center gap-3 text-left"
                    >
                      <PresenceAvatar
                        label={avatarInitial(label)}
                        url={u?.avatar_url}
                        sizeClass="size-9"
                        textClass="text-sm"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-foreground">
                          {label}
                        </span>
                        {r.message && (
                          <span className="block truncate text-xs text-muted-foreground">
                            {r.message}
                          </span>
                        )}
                      </span>
                    </button>
                    <div className="flex shrink-0 gap-1.5">
                      <Button
                        variant="neutral"
                        size="sm"
                        disabled={busyId !== null}
                        onClick={() => void handleReject(r.id)}
                      >
                        拒绝
                      </Button>
                      <Button
                        size="sm"
                        disabled={busyId !== null}
                        onClick={() => void handleAccept(r.id)}
                      >
                        同意
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}

          {pendingOutgoing.length > 0 && (
            <>
              <p className="border-t border-border px-5 pb-1 pt-3 text-xs font-medium text-muted-foreground">
                发出的申请
              </p>
              <ul className="pb-2">
                {pendingOutgoing.map((r) => {
                  const u = r.peer;
                  const label =
                    u?.display_name || u?.username || r.to_user_id.slice(0, 8);
                  return (
                    <li key={r.id}>
                      <Button
                        variant="ghost"
                        onClick={() => onOpenProfile(r.to_user_id)}
                        className="h-auto w-full justify-start gap-3 rounded-none px-5 py-2 font-normal"
                      >
                        <PresenceAvatar
                          label={avatarInitial(label)}
                          url={u?.avatar_url}
                          sizeClass="size-9"
                          textClass="text-sm"
                        />
                        <span className="flex min-w-0 flex-1 flex-col text-left">
                          <span className="truncate text-sm text-foreground">
                            {label}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            等待对方处理
                          </span>
                        </span>
                      </Button>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
