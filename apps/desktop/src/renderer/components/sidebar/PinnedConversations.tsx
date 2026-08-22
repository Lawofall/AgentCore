import { useConversations } from "@/hooks/useConversations";
import type { Conversation } from "@/stores/conversation";
import { useMemo } from "react";
import { ConversationItem } from "./ConversationItem";

function byRecency(a: Conversation, b: Conversation): number {
  return (Date.parse(b.updatedAt) || 0) - (Date.parse(a.updatedAt) || 0);
}

/**
 * Rail「置顶」zone (前端UX §一 方案C): every pinned conversation — 裸聊 and
 * foldered alike — floats above the folder groups. No section title; the zone is
 * absent when nothing is pinned. Pinned rows are excluded from
 * {@link WorkspaceGroups} / {@link RecentConversations} so each chat has one home.
 */
export function PinnedConversations({
  onActivate,
}: {
  onActivate?: () => void;
}) {
  const conversations = useConversations();

  const pinned = useMemo(
    () => conversations.filter((c) => c.pinned).sort(byRecency),
    [conversations],
  );

  if (pinned.length === 0) return null;

  return (
    <div className="space-y-0.5 px-2 pt-2 pb-1">
      {pinned.map((conv) => (
        <ConversationItem
          key={conv.id}
          conversation={conv}
          onActivate={onActivate}
        />
      ))}
    </div>
  );
}
