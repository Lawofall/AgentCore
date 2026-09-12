import { Button } from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useIncomingFriendRequestCount,
  useMessagingStore,
} from "@/stores/messaging";
import { ChevronRight, UserPlus, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { FriendRequestsDialog } from "./FriendRequestsDialog";
import { PresenceAvatar } from "./PresenceAvatar";
import { avatarInitial } from "./chatDisplay";

interface Props {
  open: boolean;
  onClose: () => void;
  onOpenProfile: (userId: string) => void;
}

/**
 * 通讯录 (消息IM.md §9.4): accepted friends list + 「新的朋友」申请箱入口.
 */
export function ContactsDialog({ open, onClose, onOpenProfile }: Props) {
  const friends = useMessagingStore((s) => s.friends);
  const loaded = useMessagingStore((s) => s.friendsLoaded);
  const fetchFriends = useMessagingStore((s) => s.fetchFriends);
  const fetchFriendRequests = useMessagingStore((s) => s.fetchFriendRequests);
  const incomingCount = useIncomingFriendRequestCount();
  const [requestsOpen, setRequestsOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    void fetchFriends();
    void fetchFriendRequests();
  }, [open, fetchFriends, fetchFriendRequests]);

  useEffect(() => {
    if (!open) setRequestsOpen(false);
  }, [open]);

  return (
    <>
      <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
        <DialogContent size="md" aria-describedby={undefined}>
          <DialogHeader>
            <DialogTitle>通讯录</DialogTitle>
          </DialogHeader>

          <DialogBody className="max-h-96 px-0">
            <Button
              variant="ghost"
              onClick={() => setRequestsOpen(true)}
              className="h-auto w-full justify-start gap-3 rounded-none px-5 py-3 font-normal"
            >
              <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                <UserPlus size={16} />
              </span>
              <span className="flex min-w-0 flex-1 items-center justify-between">
                <span className="text-sm text-foreground">新的朋友</span>
                <span className="flex items-center gap-1.5">
                  {incomingCount > 0 && (
                    <span className="rounded-full bg-primary/15 px-1.5 py-0.5 text-xs font-medium text-primary">
                      {incomingCount > 9 ? "9+" : incomingCount}
                    </span>
                  )}
                  <ChevronRight
                    size={14}
                    className="text-muted-foreground"
                    aria-hidden
                  />
                </span>
              </span>
            </Button>

            <div className="border-t border-border">
              <p className="flex items-center gap-1.5 px-5 pb-1 pt-3 text-xs font-medium text-muted-foreground">
                <Users size={12} aria-hidden />
                好友
              </p>
              {!loaded && friends.length === 0 ? (
                <p className="px-5 py-6 text-center text-sm text-muted-foreground">
                  加载中…
                </p>
              ) : friends.length === 0 ? (
                <p className="px-5 py-6 text-center text-sm text-muted-foreground">
                  还没有好友，可从群聊头像或搜人加好友
                </p>
              ) : (
                <ul className="pb-2">
                  {friends.map((f) => (
                    <li key={f.id}>
                      <Button
                        variant="ghost"
                        onClick={() => {
                          onOpenProfile(f.id);
                        }}
                        className="h-auto w-full justify-start gap-3 rounded-none px-5 py-2 font-normal"
                      >
                        <PresenceAvatar
                          label={avatarInitial(f.display_name || f.username)}
                          url={f.avatar_url}
                          sizeClass="size-9"
                          textClass="text-sm"
                          online={!!f.online}
                        />
                        <span className="flex min-w-0 flex-1 flex-col text-left">
                          <span className="truncate text-sm text-foreground">
                            {f.display_name || f.username}
                          </span>
                          <span className="truncate text-xs text-muted-foreground">
                            @{f.username}
                          </span>
                        </span>
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </DialogBody>
        </DialogContent>
      </Dialog>

      <FriendRequestsDialog
        open={requestsOpen}
        onClose={() => setRequestsOpen(false)}
        onOpenProfile={onOpenProfile}
      />
    </>
  );
}
