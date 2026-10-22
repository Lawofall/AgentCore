import { Button } from "@/components/ui";
import {
  useExternalGrants,
  useRevokeExternalGrant,
} from "@/hooks/useExternalGrants";
import { hasLocalFiles } from "@/lib/capabilities";
import { pickAndGrantConversationFolder } from "@/lib/grantOrganizeFolder";
import { notifyError } from "@/lib/toast";
import { externalGrantModeLabel } from "@/services/externalGrants";
import { FolderOpen, FolderPlus, Link2Off } from "lucide-react";
import { useState } from "react";

/**
 * Conversation folder grants: working root ("正在用") plus extra folders.
 * Empty on desktop still shows「添加文件夹」— scratch chats start with no cwd.
 * Never shows absolute paths.
 */
export function ExternalMountsSection({
  conversationId,
}: {
  conversationId: string;
}) {
  const grantsQuery = useExternalGrants(conversationId);
  const revoke = useRevokeExternalGrant(conversationId);
  const grants = grantsQuery.data ?? [];
  const desktop = hasLocalFiles();
  const [adding, setAdding] = useState(false);

  const addFolder = async () => {
    setAdding(true);
    try {
      const result = await pickAndGrantConversationFolder(conversationId);
      if (!result.ok && result.reason !== "cancelled") {
        if (result.reason === "unavailable") return;
        notifyError(new Error(result.message), "添加文件夹失败");
      }
    } finally {
      setAdding(false);
    }
  };

  if (!grantsQuery.isError && grants.length === 0 && !desktop) return null;

  if (grantsQuery.isError) {
    return (
      <div className="shrink-0 border-t border-border px-3 py-2">
        <p className="text-xs text-muted-foreground">
          无法加载已授权文件夹
          <Button
            size="sm"
            variant="ghost"
            className="ml-1"
            onClick={() => void grantsQuery.refetch()}
          >
            重试
          </Button>
        </p>
      </div>
    );
  }

  return (
    <div className="shrink-0 border-t border-border">
      <div className="flex items-center gap-1.5 px-3 py-1.5">
        <FolderOpen size={12} className="text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-muted-foreground">
          正在用的文件夹
        </span>
        {desktop ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={adding}
            title="添加文件夹"
            onClick={() => void addFolder()}
            icon={<FolderPlus size={12} />}
          >
            添加
          </Button>
        ) : null}
      </div>
      {grants.length === 0 ? (
        <p className="px-3 pb-2 text-xs text-muted-foreground">
          还没有授权文件夹。添加后这一场就在那个目录里干活，不改对话所属项目。
        </p>
      ) : (
        <ul className="space-y-0.5 px-2 pb-2">
          {grants.map((g) => (
            <li
              key={g.root_id}
              className="flex items-center gap-1.5 rounded-lg px-1.5 py-1"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium">{g.label}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {g.working_root ? "正在用" : "额外"} ·{" "}
                  {externalGrantModeLabel(g.mode)}
                </p>
              </div>
              <Button
                size="sm"
                variant="ghost"
                disabled={revoke.isPending}
                title="撤销"
                onClick={() =>
                  revoke.mutate(g.root_id, {
                    onError: (err) => notifyError(err, "撤销失败"),
                  })
                }
                icon={<Link2Off size={12} />}
              >
                撤销
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
