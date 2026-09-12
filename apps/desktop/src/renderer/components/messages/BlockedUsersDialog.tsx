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
  type BlockedUser,
  listBlocks,
  messagingErrorMessage,
  unblockUser,
} from "@/services/messaging";
import { useEffect, useState } from "react";
import { PresenceAvatar } from "./PresenceAvatar";
import { avatarInitial } from "./chatDisplay";

interface Props {
  open: boolean;
  onClose: () => void;
}

/** 已拉黑用户列表 — 消息隐私设置入口 (消息IM.md §9.4). */
export function BlockedUsersDialog({ open, onClose }: Props) {
  const [users, setUsers] = useState<BlockedUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    void listBlocks()
      .then(setUsers)
      .catch((e) => notifyError(e, "加载拉黑列表失败"))
      .finally(() => setLoading(false));
  }, [open]);

  const handleUnblock = async (id: string) => {
    setBusyId(id);
    try {
      await unblockUser(id);
      setUsers((prev) => prev.filter((u) => u.id !== id));
    } catch (err) {
      notifyError(err, messagingErrorMessage(err, "操作失败"));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent size="md" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>已拉黑</DialogTitle>
        </DialogHeader>
        <DialogBody className="max-h-96 px-0">
          {loading ? (
            <p className="px-5 py-6 text-center text-sm text-muted-foreground">
              加载中…
            </p>
          ) : users.length === 0 ? (
            <p className="px-5 py-6 text-center text-sm text-muted-foreground">
              暂无拉黑用户
            </p>
          ) : (
            <ul className="py-1">
              {users.map((u) => (
                <li key={u.id} className="flex items-center gap-3 px-5 py-2">
                  <PresenceAvatar
                    label={avatarInitial(u.display_name || u.username)}
                    url={u.avatar_url}
                    sizeClass="size-9"
                    textClass="text-sm"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-foreground">
                      {u.display_name || u.username}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">
                      @{u.username}
                    </span>
                  </span>
                  <Button
                    variant="neutral"
                    size="sm"
                    disabled={busyId !== null}
                    onClick={() => void handleUnblock(u.id)}
                  >
                    取消拉黑
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
