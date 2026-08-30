/**
 * Conversation-level decision prompts. Unified DecisionCard shell; single mount
 * in ChatView above the composer. Full-screen turn detail is look-only and has
 * no command bar (协作图与双视图UX.md §六 两个入口：聊天内嵌 ⇄ 全屏放大).
 */
import { ApprovalPrompt } from "./ApprovalPrompt";
import { EscalationPrompt } from "./EscalationPrompt";
import { ResumePrompt } from "./ResumePrompt";
import { ResumeSettledNotices } from "./ResumeSettledNotices";
import { RunConfirmPrompt } from "./RunConfirmPrompt";
import { SettledElsewhereNotices } from "./SettledElsewhereNotices";

export function ConversationDecisionPrompts() {
  return (
    <>
      <SettledElsewhereNotices />
      <ResumeSettledNotices />
      <ResumePrompt />
      <EscalationPrompt />
      <ApprovalPrompt />
      <RunConfirmPrompt />
    </>
  );
}
