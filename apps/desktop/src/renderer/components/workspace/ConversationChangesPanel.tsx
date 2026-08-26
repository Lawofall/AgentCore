import { TurnFileChangesReview } from "@/components/chat/TurnFileChangesReview";
import { EmptyHint } from "@/components/files/parts";
import { GitChangesSection } from "@/components/workspace/GitChangesSection";
import { useWorkspaceModeState } from "@/components/workspace/WorkspaceModeControl";
import { useGitRepoStatus } from "@/hooks/useGitRepoStatus";
import { useLocalTurnBaselineIds } from "@/hooks/useLocalTurnBaselineIds";
import { useConversationWorkspace } from "@/hooks/useWorkspaces";
import { hasLocalFiles } from "@/lib/capabilities";
import { shouldIncludeChangesTurn } from "@/lib/conversationFileChanges";
import {
  type FileArtifact,
  fileArtifactsFromExecution,
  fileArtifactsFromProcess,
  mergeArtifacts,
} from "@/lib/fileArtifacts";
import { formatMessageTime } from "@/lib/format";
import { gitTrackHasWork } from "@/lib/gitRepoStatus";
import { useAutoSnapshotStore } from "@/stores/autoSnapshot";
import { useConversationStore } from "@/stores/conversation";
import {
  assistantProjectionId,
  runtimeOf,
} from "@/stores/conversation/runtime";
import { projectRuntime, useExecutionStore } from "@/stores/execution";
import { useSidePanelStore } from "@/stores/sidePanel";
import { Diff } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

/**
 * 右坞「改动」tab 体 —— 只审本对话 AI 文件改动：回合 diff + 回合基线回滚，
 * 本机有仓时并排 Git SCM（U2/U3）。用户留存版本不在这里。
 * 只读 process / execution，不为「出现产物」invalidate 工作区列表或换 FileSource。
 *
 * tab 出现条件由外层决定；深链只决定聚焦哪个回合。
 */

interface TurnEntry {
  id: string;
  messageId: string;
  label: string;
  artifacts: FileArtifact[];
  at: string;
}

/**
 * 回合后自动备份失败的横幅 —— SSE 只翻 `useAutoSnapshotStore` 的位，这里是它唯一的
 * UI 出口（原挂在已下线的快照面板顶部）。回合本身是成功的，所以是提醒不是报错。
 */
function AutoBackupFailedNotice({
  conversationId,
}: { conversationId: string }) {
  const failed = useAutoSnapshotStore((s) =>
    Boolean(s.failedByConversation[conversationId]),
  );
  if (!failed) return null;
  return (
    <div className="shrink-0 border-b border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
      最近一次自动备份失败。回合已正常完成，下次改文件的回合会再试。
    </div>
  );
}

export function ConversationChangesPanel() {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const messages = useConversationStore(
    (s) => runtimeOf(s, conversationId).messages,
  );
  const byId = useExecutionStore((s) => s.byId);
  const focusMessageId = useSidePanelStore((s) => s.changesFocusMessageId);
  const baselineMessageIds = useLocalTurnBaselineIds(conversationId, messages);

  const wsState = useWorkspaceModeState(conversationId);
  const convWs = useConversationWorkspace(conversationId);
  const canGit =
    hasLocalFiles() &&
    !!wsState?.effective.isLocal &&
    !!wsState.effective.rootId &&
    !wsState.effective.rootMissing;
  const rootId = canGit ? (wsState?.effective.rootId ?? null) : null;
  // FileDetail / createLocalRootSource 期望 workspace 相对路径；git 仍在仓根跑。
  const workspaceSubpath = convWs?.subpath ?? "";
  const { status: gitStatus, refresh: refreshGit } = useGitRepoStatus(
    rootId,
    canGit,
  );
  const showGitTrack = gitTrackHasWork(gitStatus);

  const turns = useMemo((): TurnEntry[] => {
    const out: TurnEntry[] = [];
    let turnIndex = 0;
    for (const msg of messages) {
      if (msg.role !== "assistant") continue;
      turnIndex += 1;
      const messageId = assistantProjectionId(msg);
      const rt = byId[messageId];
      const execution = rt ? projectRuntime(rt) : null;
      const artifacts = mergeArtifacts(
        fileArtifactsFromProcess(msg.process),
        fileArtifactsFromExecution(execution),
      );
      if (
        !shouldIncludeChangesTurn({
          artifactsLength: artifacts.length,
          messageId,
          baselineMessageIds,
          focusMessageId,
        })
      ) {
        continue;
      }
      out.push({
        id: messageId,
        messageId,
        label: `回合 ${turnIndex}`,
        artifacts,
        at: msg.createdAt,
      });
    }
    // 聚焦回合尚未出现在 messages（极端时序）时仍给一个入口。
    if (focusMessageId && !out.some((t) => t.messageId === focusMessageId)) {
      out.push({
        id: focusMessageId,
        messageId: focusMessageId,
        label: "本回合",
        artifacts: [],
        at: new Date().toISOString(),
      });
    }
    return out;
  }, [messages, byId, focusMessageId, baselineMessageIds]);

  // 倒序：最近回合在上（原先 zip 时间轴也是倒序，只是不再穿插版本）。
  const timeline = useMemo(() => [...turns].reverse(), [turns]);

  const focusRef = useRef<HTMLElement | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: timeline is an intentional re-run key after list lands
  useEffect(() => {
    if (!focusMessageId) return;
    focusRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focusMessageId, timeline]);

  if (!conversationId) {
    return (
      <EmptyHint
        inline
        icon={<Diff size={26} className="text-muted-foreground/40" />}
        title="暂无改动"
        hint="发送消息后，本对话 AI 写入工作区的文件改动或可恢复基线会出现在这里。"
      />
    );
  }

  if (timeline.length === 0 && !showGitTrack) {
    return (
      <div className="flex h-full flex-col">
        <AutoBackupFailedNotice conversationId={conversationId} />
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
          <Diff size={26} className="text-muted-foreground/40" />
          <div className="space-y-1">
            <p className="text-sm text-muted-foreground">暂无改动</p>
            <p className="text-xs text-muted-foreground">
              本对话尚无 AI 文件改动，也没有可恢复的回合基线。
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <AutoBackupFailedNotice conversationId={conversationId} />
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        {showGitTrack && rootId && gitStatus ? (
          <GitChangesSection
            rootId={rootId}
            status={gitStatus}
            onRefresh={() => void refreshGit()}
            subpath={workspaceSubpath}
          />
        ) : null}

        <div className="space-y-3" data-testid="changes-timeline">
          {showGitTrack ? (
            <p className="px-0.5 text-xs text-muted-foreground">
              本对话改动（与 Git 正交）
            </p>
          ) : null}

          {timeline.map((entry) => {
            const focused = entry.messageId === focusMessageId;
            return (
              <section
                key={entry.id}
                ref={focused ? focusRef : undefined}
                data-testid="changes-timeline-entry"
                data-entry-kind="turn"
                data-entry-id={entry.id}
                className={`rounded-xl border border-border bg-card ${
                  focused ? "ring-1 ring-primary/40" : ""
                }`}
              >
                <TurnFileChangesReview
                  artifacts={entry.artifacts}
                  conversationId={conversationId}
                  messageId={entry.messageId}
                  variant="panel"
                  heading={entry.label}
                  headingTime={formatMessageTime(entry.at)}
                />
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}
