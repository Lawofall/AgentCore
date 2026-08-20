import type { ChatMessageDetail, ChatSummary } from "@/services/messaging";
import { describe, expect, it } from "vitest";
import {
  EVERYONE_MENTION_LABEL,
  OFFICIAL_CHAT_DISPLAY_NAME,
  avatarSrc,
  bubbleAvatarUrl,
  buildReplySnapshot,
  canActAsGroupModerator,
  canModerateMemberTarget,
  canOfferEdit,
  canOfferRecall,
  chatCircleAvatarUrl,
  chatDisplayName,
  filterMentionsInContent,
  findImMentionDraft,
  findOfficialChatId,
  isGroupModeratorRole,
  memberGovernanceBadge,
  memberGovernanceBadges,
  mentionAtToken,
  mentionRoleSubtitle,
  messageMentionsUser,
  replyBodyPreview,
  splitContentByMentions,
  truncateReplyPreview,
} from "../chatDisplay";

function chat(
  partial: Partial<ChatSummary> & Pick<ChatSummary, "id" | "type">,
): ChatSummary {
  return {
    muted: false,
    pinned: false,
    state: "accepted",
    unread: 0,
    ...partial,
  };
}

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
    mentions: [],
    recalled_at: null,
    recalled_by_user_id: null,
    edited_at: null,
    created_at: "2026-01-01T00:00:00Z",
    ...partial,
  };
}

describe("chatDisplayName", () => {
  it("brands the official broadcast chat", () => {
    expect(
      chatDisplayName(chat({ id: "o1", type: "official", title: "官方号" })),
    ).toBe(OFFICIAL_CHAT_DISPLAY_NAME);
  });

  it("uses peer name for dms", () => {
    expect(
      chatDisplayName(
        chat({
          id: "d1",
          type: "dm",
          peer: {
            id: "u1",
            username: "alice",
            display_name: "Alice",
            online: false,
            is_admin: false,
            group_role: "member",
            muted_by_admin: false,
          },
        }),
      ),
    ).toBe("Alice");
  });
});

describe("findOfficialChatId", () => {
  it("returns the official chat id when present", () => {
    const chats = [
      chat({ id: "g1", type: "group", title: "内测群" }),
      chat({ id: "o1", type: "official", pinned: true }),
    ];
    expect(findOfficialChatId(chats)).toBe("o1");
  });

  it("returns null when absent", () => {
    expect(findOfficialChatId([chat({ id: "g1", type: "group" })])).toBeNull();
  });
});

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

  it("uses withdrawn label for recalled targets", () => {
    expect(
      replyBodyPreview(
        msg({
          id: "m-r",
          content: null,
          recalled_at: "2026-01-01T00:01:00Z",
        }),
      ),
    ).toBe("[已撤回]");
  });
});

describe("canOfferRecall", () => {
  it("allows own message within the window", () => {
    const recent = msg({
      id: "m1",
      created_at: new Date().toISOString(),
    });
    expect(
      canOfferRecall(recent, { mine: true, isAdmin: false, chatType: "dm" }),
    ).toBe(true);
  });

  it("denies own message after the window", () => {
    const old = msg({
      id: "m2",
      created_at: "2020-01-01T00:00:00Z",
    });
    expect(
      canOfferRecall(old, { mine: true, isAdmin: false, chatType: "dm" }),
    ).toBe(false);
  });

  it("allows admin to recall group member messages", () => {
    const old = msg({
      id: "m3",
      created_at: "2020-01-01T00:00:00Z",
    });
    expect(
      canOfferRecall(old, { mine: false, isAdmin: true, chatType: "group" }),
    ).toBe(true);
  });

  it("allows group moderator to recall group member messages", () => {
    const old = msg({
      id: "m3b",
      created_at: "2020-01-01T00:00:00Z",
    });
    expect(
      canOfferRecall(old, {
        mine: false,
        isAdmin: false,
        isGroupModerator: true,
        chatType: "group",
      }),
    ).toBe(true);
  });

  it("denies group moderator recall of system_card", () => {
    const card = msg({
      id: "m3c",
      content_type: "system_card",
      created_at: new Date().toISOString(),
    });
    expect(
      canOfferRecall(card, {
        mine: false,
        isAdmin: false,
        isGroupModerator: true,
        chatType: "group",
      }),
    ).toBe(false);
  });

  it("restricts system_card to admin", () => {
    const card = msg({
      id: "m4",
      content_type: "system_card",
      created_at: new Date().toISOString(),
    });
    expect(
      canOfferRecall(card, { mine: true, isAdmin: false, chatType: "group" }),
    ).toBe(false);
    expect(
      canOfferRecall(card, { mine: false, isAdmin: true, chatType: "group" }),
    ).toBe(true);
  });
});

describe("group governance helpers", () => {
  it("detects group moderator roles", () => {
    expect(isGroupModeratorRole("owner")).toBe(true);
    expect(isGroupModeratorRole("admin")).toBe(true);
    expect(isGroupModeratorRole("member")).toBe(false);
    expect(canActAsGroupModerator(true, "member")).toBe(true);
    expect(canActAsGroupModerator(false, "admin")).toBe(true);
    expect(canActAsGroupModerator(false, "member")).toBe(false);
  });

  it("badges platform admin over group role", () => {
    expect(
      memberGovernanceBadge({ is_admin: true, group_role: "admin" }),
    ).toEqual({
      kind: "platform",
      label: "平台管理员",
      shortLabel: "平台",
    });
    expect(
      memberGovernanceBadges({ is_admin: true, group_role: "owner" }),
    ).toEqual(["平台管理员"]);
    expect(
      memberGovernanceBadge({ is_admin: false, group_role: "owner" }),
    ).toEqual({ kind: "owner", label: "群主", shortLabel: "群主" });
    expect(
      memberGovernanceBadge({ is_admin: false, group_role: "admin" }),
    ).toEqual({ kind: "admin", label: "管理员", shortLabel: "管理员" });
    expect(
      memberGovernanceBadges({ is_admin: false, group_role: "member" }),
    ).toEqual([]);
  });

  it("builds @-menu subtitle with short role + handle", () => {
    expect(
      mentionRoleSubtitle({
        username: "alice",
        is_admin: false,
        group_role: "admin",
      }),
    ).toBe("管理员 · @alice");
    expect(
      mentionRoleSubtitle({
        username: "root",
        is_admin: true,
        group_role: "member",
      }),
    ).toBe("平台 · @root");
    expect(
      mentionRoleSubtitle({
        username: "bob",
        is_admin: false,
        group_role: "member",
      }),
    ).toBe("@bob");
  });

  it("hides moderate targets for self / platform / peer group mods", () => {
    expect(
      canModerateMemberTarget({
        myUserId: "me",
        isPlatformAdmin: false,
        target: {
          id: "me",
          is_admin: false,
          group_role: "member",
        },
      }),
    ).toBe(false);
    expect(
      canModerateMemberTarget({
        myUserId: "me",
        isPlatformAdmin: true,
        target: {
          id: "u2",
          is_admin: true,
          group_role: "member",
        },
      }),
    ).toBe(false);
    expect(
      canModerateMemberTarget({
        myUserId: "me",
        isPlatformAdmin: false,
        target: {
          id: "u2",
          is_admin: false,
          group_role: "admin",
        },
      }),
    ).toBe(false);
    expect(
      canModerateMemberTarget({
        myUserId: "me",
        isPlatformAdmin: true,
        target: {
          id: "u2",
          is_admin: false,
          group_role: "admin",
        },
      }),
    ).toBe(true);
    expect(
      canModerateMemberTarget({
        myUserId: "me",
        isPlatformAdmin: false,
        target: {
          id: "u2",
          is_admin: false,
          group_role: "member",
        },
      }),
    ).toBe(true);
  });
});

describe("canOfferEdit", () => {
  it("allows own recent plain-text message", () => {
    const m = msg({
      id: "e1",
      content: "hello",
      created_at: new Date().toISOString(),
    });
    expect(canOfferEdit(m, { mine: true, chatType: "dm" })).toBe(true);
  });

  it("refuses attachments, recalled, others, and timeout", () => {
    expect(
      canOfferEdit(
        msg({
          id: "e2",
          content: "x",
          attachments: [
            {
              name: "a.png",
              path: "a.png",
              kind: "file",
              binary: true,
              truncated: false,
            },
          ],
          created_at: new Date().toISOString(),
        }),
        { mine: true, chatType: "dm" },
      ),
    ).toBe(false);
    expect(
      canOfferEdit(
        msg({
          id: "e3",
          content: "x",
          recalled_at: new Date().toISOString(),
          created_at: new Date().toISOString(),
        }),
        { mine: true, chatType: "dm" },
      ),
    ).toBe(false);
    expect(
      canOfferEdit(
        msg({
          id: "e4",
          content: "x",
          created_at: new Date().toISOString(),
        }),
        { mine: false, chatType: "dm" },
      ),
    ).toBe(false);
    expect(
      canOfferEdit(
        msg({
          id: "e5",
          content: "x",
          created_at: "2020-01-01T00:00:00Z",
        }),
        { mine: true, chatType: "dm" },
      ),
    ).toBe(false);
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

  it("builds @ tokens and filters deleted body tokens", () => {
    expect(mentionAtToken({ kind: "everyone" }, names)).toBe(
      `@${EVERYONE_MENTION_LABEL}`,
    );
    expect(mentionAtToken({ kind: "user", user_id: "u1" }, names)).toBe(
      "@Alice",
    );
    expect(
      filterMentionsInContent(
        "hi @Alice and more",
        [
          { kind: "user", user_id: "u1" },
          { kind: "user", user_id: "u2" },
          { kind: "everyone" },
        ],
        names,
      ),
    ).toEqual([{ kind: "user", user_id: "u1" }]);
  });

  it("splits content by structured mention tokens", () => {
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

  it("finds an active @ draft at the caret", () => {
    expect(findImMentionDraft("hello @Al", 9)).toEqual({
      start: 6,
      end: 9,
      query: "Al",
    });
    expect(findImMentionDraft("hello@Al", 8)).toBeNull();
    expect(findImMentionDraft("@", 1)).toEqual({
      start: 0,
      end: 1,
      query: "",
    });
  });
});

describe("avatarSrc", () => {
  it("prefixes a relative path and leaves null / absolute alone", () => {
    expect(avatarSrc(null)).toBeNull();
    expect(avatarSrc(undefined)).toBeNull();
    expect(avatarSrc("")).toBeNull();
    expect(avatarSrc("/v1/users/u1/avatar?v=a")).toBe(
      "http://localhost:8000/v1/users/u1/avatar?v=a",
    );
    expect(avatarSrc("https://cdn.example/a.png")).toBe(
      "https://cdn.example/a.png",
    );
  });
});

describe("chatCircleAvatarUrl", () => {
  it("uses peer.avatar_url for dms, never chat.avatar_url", () => {
    expect(
      chatCircleAvatarUrl(
        chat({
          id: "d1",
          type: "dm",
          avatar_url: "/v1/chats/d1/avatar",
          peer: {
            id: "u1",
            username: "alice",
            display_name: "Alice",
            online: false,
            is_admin: false,
            group_role: "member",
            muted_by_admin: false,
            avatar_url: "/v1/users/u1/avatar?v=1",
          },
        }),
      ),
    ).toBe("/v1/users/u1/avatar?v=1");
    expect(
      chatCircleAvatarUrl(
        chat({
          id: "d1",
          type: "dm",
          avatar_url: "/v1/chats/d1/avatar",
          peer: {
            id: "u1",
            username: "alice",
            display_name: "Alice",
            online: false,
            is_admin: false,
            group_role: "member",
            muted_by_admin: false,
            avatar_url: null,
          },
        }),
      ),
    ).toBeNull();
  });

  it("uses chat.avatar_url only for group / official session icons", () => {
    expect(
      chatCircleAvatarUrl(
        chat({
          id: "g1",
          type: "group",
          title: "内测群",
          avatar_url: "/v1/chats/g1/avatar",
        }),
      ),
    ).toBe("/v1/chats/g1/avatar");
    expect(
      chatCircleAvatarUrl(
        chat({ id: "o1", type: "official", avatar_url: null }),
      ),
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
