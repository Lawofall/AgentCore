import { IconButton } from "@/components/ui";
import { useConversations } from "@/hooks/useConversations";
import { useNarrowLayoutState } from "@/lib/narrowLayout";
import { startNewConversation } from "@/lib/newConversation";
import { isNarrowChatRoute } from "@/lib/useNarrowLayout";
import { useConversationStore } from "@/stores/conversation";
import { useSidePanelStore } from "@/stores/sidePanel";
import { Menu, PanelRight, SquarePen } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";

export function NarrowTopBar() {
  const { isNarrow, hideChrome, setConversationDrawerOpen } =
    useNarrowLayoutState();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const conversations = useConversations();
  const openPanel = useSidePanelStore((s) => s.openPanel);
  const panelOpen = useSidePanelStore((s) => s.open);

  if (!isNarrow || hideChrome || !isNarrowChatRoute(pathname)) return null;

  const title =
    (conversationId &&
      conversations.find((c) => c.id === conversationId)?.title) ||
    "新对话";

  return (
    <header className="flex h-12 shrink-0 items-center gap-1 border-b border-border bg-card px-2 pt-[env(safe-area-inset-top)]">
      <IconButton
        size="md"
        aria-label="对话列表"
        onClick={() => setConversationDrawerOpen(true)}
      >
        <Menu size={18} />
      </IconButton>
      <h1 className="min-w-0 flex-1 truncate text-center text-sm font-medium">
        {title}
      </h1>
      {conversationId && !panelOpen && (
        <IconButton size="md" aria-label="打开面板" onClick={() => openPanel()}>
          <PanelRight size={18} />
        </IconButton>
      )}
      <IconButton
        size="md"
        aria-label="新对话"
        onClick={() => startNewConversation(navigate)}
      >
        <SquarePen size={18} />
      </IconButton>
    </header>
  );
}
