import { ConversationReplay } from "@/components/ConversationReplay";
import { Navigate, useLocation, useNavigate, useParams } from "react-router-dom";

/** Standalone 会话复盘 route — bookmarkable `/replay/:conversationId`. */
export function ReplayPage() {
  const { conversationId } = useParams<{ conversationId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  /**
   * Drill-ins carry their origin (roster page, filters and all) in router state so
   * 返回 lands back on the exact list the operator left. A pasted or bookmarked
   * `/replay/:id` has no such state: fall back to 对话, which is the section the
   * sidebar already lights up for this route — 总览 sent you somewhere you were
   * demonstrably not.
   */
  const from =
    (location.state as { from?: string } | null)?.from ?? "/conversations";

  if (!conversationId) {
    return <Navigate to="/conversations" replace />;
  }

  return (
    <ConversationReplay
      conversationId={conversationId}
      backLabel="返回"
      onBack={() => navigate(from)}
    />
  );
}
