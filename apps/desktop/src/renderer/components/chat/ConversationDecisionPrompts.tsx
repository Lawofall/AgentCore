/**
 * Conversation-level decision prompts. Unified DecisionCard shell; single mount
 * in ChatView above the composer. Full-screen turn detail is look-only and has
 * no command bar (协作图与双视图UX.md §六 两个入口：聊天内嵌 ⇄ 全屏放大).
 *
 * Chat may omit ApprovalPrompt here and remount it flush above MessageInput
 * (composer 一体态).
 */
import { ApprovalPrompt } from "./ApprovalPrompt";
import { ResumePrompt } from "./ResumePrompt";
import { ResumeSettledNotices } from "./ResumeSettledNotices";
import { RunConfirmPrompt } from "./RunConfirmPrompt";
import { SettledElsewhereNotices } from "./SettledElsewhereNotices";

export function ConversationDecisionPrompts({
  omitApproval = false,
}: {
  /**
   * When true, skip {@link ApprovalPrompt} here — ChatView mounts it flush above
   * MessageInput for composer-一体态 (仍同一组件 / 同一 interactions 热路).
   */
  omitApproval?: boolean;
}) {
  return (
    <>
      {/* 卡被另一端拍板后留在原位的只读收口——不随 omitApproval 走，它交代的是
          决策区里刚消失的**任意**一张卡（含 ChatView 另挂的审批卡）。 */}
      <SettledElsewhereNotices />
      {/* 点下去才发现这张卡早被处理过（冷 resume 幂等成功）——同样留在原位交代，
          中性/信息态，不是故障。 */}
      <ResumeSettledNotices />
      <ResumePrompt />
      {!omitApproval && <ApprovalPrompt />}
      <RunConfirmPrompt />
    </>
  );
}
