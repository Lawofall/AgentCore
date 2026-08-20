// @vitest-environment jsdom
/**
 * IM bubble width: shrink-wrap + dual cap (industry IM), not stretch-to-75%.
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import type { ImBubbleLayout } from "@/lib/imMessageLayout";
import type { ChatMessageDetail } from "@/services/messaging";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ChatBubble } from "../ChatBubble";

afterEach(cleanup);

const layout: ImBubbleLayout = {
  clusterPosition: "single",
  showAvatar: true,
  showSenderName: false,
  tightTop: false,
};

function baseMessage(over: Partial<ChatMessageDetail> = {}): ChatMessageDetail {
  return {
    id: "m1",
    chat_id: "c1",
    content: "你好",
    content_type: "text",
    created_at: "2026-08-10T10:00:00Z",
    sender_type: "user",
    sender_user_id: "u1",
    payload: null,
    attachments: [],
    mentions: [],
    ...over,
  };
}

function renderBubble(message: ChatMessageDetail, mine: boolean) {
  return render(
    <TooltipProvider>
      <ChatBubble
        message={message}
        mine={mine}
        avatarName="测"
        layout={layout}
      />
    </TooltipProvider>,
  );
}

describe("ChatBubble width", () => {
  it("shrink-wraps with min(75%, 24rem) cap on the row (mine)", () => {
    const { container } = renderBubble(baseMessage(), true);
    const row = container.querySelector("[data-message-id='m1']");
    expect(row?.className).toContain("w-fit");
    expect(row?.className).toContain("max-w-[min(75%,24rem)]");
    expect(row?.className).not.toMatch(/(?:^|\s)max-w-\[75%\](?:\s|$)/);
  });

  it("shrink-wraps peer text bubble without flex-1 stretch", () => {
    const { container } = renderBubble(
      baseMessage({
        content: "长文 ".repeat(40),
        reply_to: {
          sender_user_id: "u2",
          sender_display_name: "对方",
          body_preview: "被引用的预览 ".repeat(10),
        },
        reply_to_message_id: "m0",
      }),
      false,
    );
    const row = container.querySelector("[data-message-id='m1']");
    expect(row?.className).toContain("w-fit");
    expect(row?.className).toContain("max-w-[min(75%,24rem)]");
    const textBubble = row?.querySelector(".whitespace-pre-wrap");
    expect(textBubble?.className).toContain("w-fit");
    expect(textBubble?.className).toContain("max-w-full");
    expect(textBubble?.className).toContain("[overflow-wrap:anywhere]");
    // Peer column must not flex-grow into the thread width.
    const bodyCol = textBubble?.parentElement?.parentElement;
    expect(bodyCol?.className).not.toContain("flex-1");
  });

  it("clamps the reply quote to two lines, isolated from bubble pre-wrap", () => {
    const preview = "被引用的预览 ".repeat(20);
    const { container } = renderBubble(
      baseMessage({
        content: "短回复",
        reply_to: {
          sender_user_id: "u2",
          sender_display_name: "对方",
          body_preview: preview,
        },
        reply_to_message_id: "m0",
      }),
      false,
    );
    const quoteBody = [...container.querySelectorAll("span")].find(
      (el) => el.textContent === preview,
    );
    expect(quoteBody?.className).toContain("line-clamp-2");
    expect(quoteBody?.className).not.toContain("truncate");
    expect(quoteBody?.parentElement?.className).toContain("whitespace-normal");
    expect(quoteBody?.parentElement?.className).toContain("overflow-hidden");
  });
});
