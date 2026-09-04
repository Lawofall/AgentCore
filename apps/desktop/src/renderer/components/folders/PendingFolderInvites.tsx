import { Badge, Button } from "@/components/ui";
import {
  useAcceptFolderInvite,
  usePendingFolderInvites,
  useRejectFolderInvite,
} from "@/hooks/useFolderSharing";
import { notifyError } from "@/lib/toast";
import { type FolderMeta, folderRoleLabel } from "@/services/folders";
import { Users } from "lucide-react";

/**
 * REST-backed pending folder-invite strip for the files rail. Firehose only
 * nudges; this list is the durable discovery path.
 */
export function PendingFolderInvites({
  onAccepted,
}: {
  onAccepted?: (folder: FolderMeta) => void;
}) {
  const { data, isLoading, isError, refetch } = usePendingFolderInvites();
  const accept = useAcceptFolderInvite();
  const reject = useRejectFolderInvite();
  const invites = data ?? [];

  if (isLoading && invites.length === 0) return null;

  if (isError && invites.length === 0) {
    return (
      <div className="space-y-1 border-b border-border px-2 py-2">
        <p className="text-xs text-muted-foreground">无法加载协作桌邀请</p>
        <Button variant="ghost" size="sm" onClick={() => void refetch()}>
          重试
        </Button>
      </div>
    );
  }

  if (invites.length === 0) return null;

  return (
    <div className="space-y-1.5 border-b border-border px-2 py-2">
      <div className="flex items-center gap-1.5 px-1">
        <Users size={12} className="text-muted-foreground" />
        <span className="text-xs font-medium text-muted-foreground">
          待处理邀请
        </span>
        <Badge tone="muted" className="ml-auto">
          {invites.length}
        </Badge>
      </div>
      <ul className="space-y-1">
        {invites.map((inv) => {
          const busy =
            (accept.isPending && accept.variables === inv.id) ||
            (reject.isPending && reject.variables === inv.id);
          const role = inv.myRole ?? "editor";
          return (
            <li key={inv.id} className="rounded-lg bg-muted/40 px-2.5 py-2">
              <div className="flex min-w-0 items-start gap-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{inv.name}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    角色：{folderRoleLabel(role)}
                  </p>
                </div>
                <div className="flex shrink-0 gap-1">
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      accept.mutate(inv.id, {
                        onSuccess: (folder) => {
                          onAccepted?.(folder);
                        },
                        onError: (err) => notifyError(err, "接受邀请失败"),
                      })
                    }
                  >
                    接受
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() =>
                      reject.mutate(inv.id, {
                        onError: (err) => notifyError(err, "拒绝邀请失败"),
                      })
                    }
                  >
                    拒绝
                  </Button>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
