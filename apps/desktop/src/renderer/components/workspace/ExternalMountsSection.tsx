import { Button } from "@/components/ui";
import {
  useExternalGrants,
  useRevokeExternalGrant,
} from "@/hooks/useExternalGrants";
import { notifyError } from "@/lib/toast";
import { externalGrantModeLabel } from "@/services/externalGrants";
import { FolderOpen, Link2Off } from "lucide-react";

/**
 * C1 trust compensation: list this conversation's ``external/<alias>/`` mounts
 * and revoke (server DELETE + desktop session root). Empty → render nothing.
 * Density matches conversation file-tree rows; never shows absolute paths.
 */
export function ExternalMountsSection({
  conversationId,
}: {
  conversationId: string;
}) {
  const grantsQuery = useExternalGrants(conversationId);
  const revoke = useRevokeExternalGrant(conversationId);
  const grants = grantsQuery.data ?? [];

  // Empty / still loading with no cache: stay out of the way.
  if (!grantsQuery.isError && grants.length === 0) return null;

  if (grantsQuery.isError) {
    return (
      <div className="shrink-0 border-t border-border px-3 py-2">
        <p className="text-xs text-muted-foreground">
          无法加载区外挂载
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
          区外目录挂载
        </span>
      </div>
      <ul className="space-y-0.5 px-2 pb-2">
        {grants.map((g) => (
          <li
            key={g.root_id}
            className="flex items-center gap-1.5 rounded-lg px-1.5 py-1"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium">{g.label}</p>
              <p className="truncate text-xs text-muted-foreground">
                {g.namespace} · {externalGrantModeLabel(g.mode)}
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
    </div>
  );
}
