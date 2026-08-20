import type { ChatMessageDetail } from "@/api/messaging";
import { describe, expect, it } from "vitest";
import {
  EVERYONE_MENTION_LABEL,
  bubbleAvatarUrl,
  buildReplySnapshot,
  memberGovernanceBadge,
  mentionAtToken,
  messageMentionsUser,
  replyBodyPreview,
  splitContentByMentions,
  truncateReplyPreview,
} from "../chatDisplay";

function msg(
  partial: Partial<ChatMessageDetail> & Pick<ChatMessageDetail, "id">,
): ChatMessageDetail {
  return {
    chat_id: "c1",
    sender_user_id: "u1",
    sender_type: "user",
    content: null,
    content_type: "text",
    attachments: [],
    payload: null,
    reply_to_message_id: null,
    reply_to: null,
    created_at: "2026-01-01T00:00:00Z",
    ...partial,
  };
}

describe("reply preview helpers", () => {
  it("truncates long text with an ellipsis", () => {
    const exact = "a".repeat(100);
    expect(truncateReplyPreview(exact)).toBe(exact);
    const long = "a".repeat(101);
    expect(truncateReplyPreview(long).endsWith("…")).toBe(true);
    expect(truncateReplyPreview(long).length).toBe(101);
  });

  it("collapses whitespace before truncating", () => {
    expect(truncateReplyPreview("hello\n\nbob   there")).toBe(
      "hello bob there",
    );
  });

  it("uses attachment labels when content is empty", () => {
    expect(
      replyBodyPreview(
        msg({
          id: "m1",
          content_type: "image",
          attachments: [
            {
              name: "a.png",
              path: "a.png",
              kind: "file",
              binary: true,
              truncated: false,
              workspace_path: "attachments/a.png",
            },
          ],
        }),
      ),
    ).toBe("[图片]");
    expect(
      replyBodyPreview(
        msg({
          id: "m2",
          content_type: "file",
          attachments: [
            {
              name: "doc.pdf",
              path: "doc.pdf",
              kind: "file",
              binary: true,
              truncated: false,
              workspace_path: "attachments/doc.pdf",
            },
          ],
        }),
      ),
    ).toBe("[文件]");
  });

  it("builds a local reply snapshot from the target message", () => {
    expect(
      buildReplySnapshot(
        msg({ id: "m3", content: "hello world", sender_user_id: "u2" }),
        "Bob",
      ),
    ).toEqual({
      sender_user_id: "u2",
      sender_display_name: "Bob",
      body_preview: "hello world",
    });
  });
});

describe("IM mention helpers", () => {
  const names = (id: string) => ({ u1: "Alice", u2: "Bob", me: "Me" })[id];

  it("detects user and everyone mentions", () => {
    expect(
      messageMentionsUser(
        { mentions: [{ kind: "user", user_id: "me" }] },
        "me",
      ),
    ).toBe(true);
    expect(
      messageMentionsUser(
        { mentions: [{ kind: "user", user_id: "u1" }] },
        "me",
      ),
    ).toBe(false);
    expect(
      messageMentionsUser({ mentions: [{ kind: "everyone" }] }, "me"),
    ).toBe(true);
    expect(messageMentionsUser({ mentions: undefined }, "me")).toBe(false);
  });

  it("builds @ tokens and splits content by structured mentions", () => {
    expect(mentionAtToken({ kind: "everyone" }, names)).toBe(
      `@${EVERYONE_MENTION_LABEL}`,
    );
    expect(mentionAtToken({ kind: "user", user_id: "u1" }, names)).toBe(
      "@Alice",
    );
    const segments = splitContentByMentions(
      "hey @Alice see @所有人",
      [{ kind: "user", user_id: "u1" }, { kind: "everyone" }],
      names,
      "me",
    );
    expect(segments).toEqual([
      { type: "text", text: "hey " },
      { type: "mention", text: "@Alice", self: false },
      { type: "text", text: " see " },
      { type: "mention", text: "@所有人", self: true },
    ]);
  });
});

describe("group governance badges", () => {
  it("splits platform / owner / admin and prefers platform", () => {
    expect(
      memberGovernanceBadge({ is_admin: true, group_role: "admin" }),
    ).toEqual({
      kind: "platform",
      label: "平台管理员",
      shortLabel: "平台",
    });
    expect(
      memberGovernanceBadge({ is_admin: false, group_role: "owner" }),
    ).toEqual({ kind: "owner", label: "群主", shortLabel: "群主" });
    expect(
      memberGovernanceBadge({ is_admin: false, group_role: "admin" }),
    ).toEqual({ kind: "admin", label: "管理员", shortLabel: "管理员" });
    expect(
      memberGovernanceBadge({ is_admin: false, group_role: "member" }),
    ).toBeNull();
  });
});

describe("bubbleAvatarUrl", () => {
  const urls = {
    myAvatarUrl: "https://api.example/me.png",
    peerAvatarUrl: "/v1/users/peer/avatar?v=1",
    memberAvatarUrl: "/v1/users/mem/avatar?v=2",
    chatAvatarUrl: "/v1/chats/c1/avatar",
  };

  it("uses auth URL for own bubbles", () => {
    expect(bubbleAvatarUrl({ mine: true, chatType: "dm", ...urls })).toBe(
      urls.myAvatarUrl,
    );
  });

  it("uses peer for dm and roster member for group, never chat.avatar_url", () => {
    expect(bubbleAvatarUrl({ mine: false, chatType: "dm", ...urls })).toBe(
      urls.peerAvatarUrl,
    );
    expect(bubbleAvatarUrl({ mine: false, chatType: "group", ...urls })).toBe(
      urls.memberAvatarUrl,
    );
    expect(
      bubbleAvatarUrl({
        mine: false,
        chatType: "dm",
        ...urls,
        peerAvatarUrl: null,
      }),
    ).toBeNull();
    expect(
      bubbleAvatarUrl({
        mine: false,
        chatType: "group",
        ...urls,
        memberAvatarUrl: null,
      }),
    ).toBeNull();
  });

  it("uses the session icon only for official bubbles", () => {
    expect(
      bubbleAvatarUrl({ mine: false, chatType: "official", ...urls }),
    ).toBe(urls.chatAvatarUrl);
  });
});
