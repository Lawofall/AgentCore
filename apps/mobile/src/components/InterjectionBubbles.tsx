import { CollapsibleUserText } from "@/components/CollapsibleUserText";
import { UserBubbleChips } from "@/components/UserBubbleChips";
import {
  interjectionStatusLabel,
  interjectionStatusTone,
  showInterjectionStatusChrome,
} from "@/lib/interjectionStatus";

export type InterjectionItem = {
  interjectionId: string;
  content: string;
  status: string;
  note?: string | null;
  attachments?: Array<{ name: string; workspacePath?: string }>;
  agentMentions?: Array<{ agentId: string; role: string }>;
};

/**
 * S2：插话气泡 + 轻量五态（fold → userInterjections；经典+协调 DURABLE）。
 * 主渲染落点在 ProcessTimeline 的 `user_interjection` marker 槽（按 id 查本组件）；
 * 旧 journal 无 marker 时由 AssistantContent 尾部回退挂载。
 * `queued`：不出用户泡（出队会补真实用户泡），改一行低权重注记；其余四态维持气泡。
 * `addressed` 只留用户泡：徽章与服务端 note 不画（结果已在图/回复）。
 * `turnClosed`：回合已收口时 `received` 派生态「未被主 Agent 读取」（不改协议枚举）。
 */
export function InterjectionBubbles({
  items,
  turnClosed = false,
}: {
  items: readonly InterjectionItem[];
  turnClosed?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <div className="interjection-timeline" data-testid="interjection-timeline">
      {items.map((item) => {
        const showChrome = showInterjectionStatusChrome(item.status);
        const tone = interjectionStatusTone(item.status);
        const label = interjectionStatusLabel(item.status, { turnClosed });

        if (item.status === "queued") {
          return (
            <div
              key={item.interjectionId}
              className="interjection-turn"
              data-testid={`interjection-bubble-${item.interjectionId}`}
            >
              <UserBubbleChips
                attachments={item.attachments}
                agentMentions={item.agentMentions}
              />
              <div
                className="interjection-queued-note"
                data-testid={`interjection-queued-note-${item.interjectionId}`}
              >
                <span
                  className={`interjection-status tone-${tone}`}
                  data-testid={`interjection-status-${item.interjectionId}`}
                >
                  {label}
                </span>
                <span
                  className="interjection-queued-preview"
                  title={item.content}
                >
                  {item.content}
                </span>
              </div>
              {item.note ? (
                <div className="interjection-note">{item.note}</div>
              ) : null}
            </div>
          );
        }

        return (
          <div
            key={item.interjectionId}
            className="interjection-turn"
            data-testid={`interjection-bubble-${item.interjectionId}`}
          >
            <UserBubbleChips
              attachments={item.attachments}
              agentMentions={item.agentMentions}
            />
            <div className="bubble user">
              <CollapsibleUserText contentKey={item.content}>
                {item.content}
              </CollapsibleUserText>
            </div>
            {showChrome ? (
              <div
                className={`interjection-status tone-${tone}`}
                data-testid={`interjection-status-${item.interjectionId}`}
              >
                {label}
              </div>
            ) : null}
            {showChrome && item.note ? (
              <div className="interjection-note">{item.note}</div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
