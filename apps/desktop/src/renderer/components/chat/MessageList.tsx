import { useConversations } from "@/hooks/useConversations";
import {
  useBackgroundTasks,
  useBackgroundTasksSync,
  useWorkspaceRootId,
} from "@/stores/backgroundTasks";
import {
  useBrowserTakeovers,
  useBrowserTakeoversSync,
} from "@/stores/browserTakeover";
import {
  useActiveMemoryUpdates,
  useActiveMessages,
  useConversationStore,
} from "@/stores/conversation";
import {
  usePermissionChanges,
  usePermissionChangesSync,
} from "@/stores/permissionChanges";
import { useMemo } from "react";
import { BackgroundTaskCard } from "./BackgroundTaskCard";
import { BrowserTakeoverCard } from "./BrowserTakeoverCard";
import { CompactionDivider } from "./CompactionDivider";
import { MemoryUpdateCard } from "./MemoryUpdateCard";
import { MessageBubble } from "./MessageBubble";
import { PermissionChangeLine } from "./PermissionChangeLine";
import { mergeTimeline } from "./messageTimeline";

// Auto-scroll lives in ChatView's useChatScroll: it owns the scroll container
// and only follows new content while the user is already at the bottom.
export function MessageList() {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const conversations = useConversations();
  const compactedThrough = conversationId
    ? (conversations.find((c) => c.id === conversationId)?.compactedThrough ??
      null)
    : null;
  const messages = useActiveMessages();
  // 后台云端任务（交接「方案 B」）：本地模式对话才同步，按时间戳并入时间线，故卡片
  // 与消息一同**原位**渲染、随对话重开重放（数据源是后端持久化的 handoff jobs）。
  useBackgroundTasksSync(conversationId);
  const tasks = useBackgroundTasks(conversationId);
  // 绑定的本地根，供成功任务的内联评审写回本地（同一对话所有卡共用，故在此读取一次下传）。
  const rootId = useWorkspaceRootId(conversationId);

  // 记忆更新对话内可见 (§1.6): offline-consolidation「记忆已更新」cards are merged into
  // the timeline by their `anchor_at` — the end of the window of turns they folded, not the
  // (much later) row-insert time — so each sits right after those turns and scrolls into
  // history as the conversation continues, instead of永久钉在尾部堆叠 (which made stale
  // cards float below every new turn).
  const memoryUpdates = useActiveMemoryUpdates();

  // L3 团队浏览器 M2 接管标记卡 (提案 D17): 同样按时间戳锚到回合末尾并入时间线；数据来自
  // GET takeovers（打开会话拉一次，刷新/回放可重建）+ 归还控制时 store 乐观合并。
  useBrowserTakeoversSync(conversationId);
  const takeovers = useBrowserTakeovers(conversationId);

  // 权限模式切换系统行 (原侧栏安全台账「权限模式 A → B」条目): 数据源是会话级审计 REST，打开
  // 会话拉一次，切换成功后由 PermissionAxesBadge 命令式重拉；按时间戳并入时间线、锚到它生效
  // 的那一回合之前。
  usePermissionChangesSync(conversationId);
  const presetChanges = usePermissionChanges(conversationId);

  const items = useMemo(
    () =>
      mergeTimeline(
        messages,
        tasks,
        memoryUpdates,
        takeovers,
        presetChanges,
        compactedThrough,
      ),
    [
      messages,
      tasks,
      memoryUpdates,
      takeovers,
      presetChanges,
      compactedThrough,
    ],
  );

  return (
    <div className="min-w-0 max-w-full space-y-6">
      {items.map((it) =>
        it.kind === "message" ? (
          <MessageBubble key={it.key} message={it.msg} />
        ) : it.kind === "task" ? (
          <BackgroundTaskCard key={it.key} job={it.job} rootId={rootId} />
        ) : it.kind === "memory" ? (
          <MemoryUpdateCard key={it.key} update={it.update} />
        ) : it.kind === "takeover" ? (
          <BrowserTakeoverCard key={it.key} takeover={it.takeover} />
        ) : it.kind === "compaction" ? (
          <CompactionDivider key={it.key} />
        ) : (
          <PermissionChangeLine key={it.key} change={it.change} />
        ),
      )}
    </div>
  );
}
