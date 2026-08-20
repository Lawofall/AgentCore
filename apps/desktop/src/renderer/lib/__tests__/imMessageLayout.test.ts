// @vitest-environment node

import {
  IM_BUBBLE_MAX_CLASS,
  IM_CLUSTER_GAP_MS,
  IM_SESSION_COLUMN_CLASS,
  buildImThreadItems,
  computeBubbleLayout,
} from "@/lib/imMessageLayout";
import type { ChatMessageDetail } from "@/services/messaging";
import { describe, expect, it, vi } from "vitest";

function msg(
  id: string,
  created_at: string,
  over: Partial<ChatMessageDetail> = {},
): ChatMessageDetail {
  return {
    id,
    chat_id: "chat-1",
    created_at,
    sender_user_id: "user-a",
    sender_type: "user",
    content_type: "text",
    content: "hello",
    ...over,
  } as ChatMessageDetail;
}

describe("computeBubbleLayout", () => {
  it("clusters same sender within 5 minutes", () => {
    const messages = [
      msg("1", "2026-07-05T10:00:00.000Z"),
      msg("2", "2026-07-05T10:02:00.000Z"),
      msg("3", "2026-07-05T10:04:00.000Z"),
    ];
    expect(computeBubbleLayout(messages, 0)).toMatchObject({
      clusterPosition: "first",
      showAvatar: true,
      showSenderName: true,
      tightTop: false,
    });
    expect(computeBubbleLayout(messages, 1)).toMatchObject({
      clusterPosition: "middle",
      showAvatar: false,
      showSenderName: false,
      tightTop: true,
    });
    expect(computeBubbleLayout(messages, 2)).toMatchObject({
      clusterPosition: "last",
      showAvatar: false,
      tightTop: true,
    });
  });

  it("breaks cluster when gap is >= 5 minutes", () => {
    const messages = [
      msg("1", "2026-07-05T10:00:00.000Z"),
      msg(
        "2",
        new Date(
          new Date("2026-07-05T10:00:00.000Z").getTime() + IM_CLUSTER_GAP_MS,
        ).toISOString(),
      ),
    ];
    expect(computeBubbleLayout(messages, 1)).toMatchObject({
      clusterPosition: "single",
      showAvatar: true,
      tightTop: false,
    });
  });

  it("breaks cluster across different senders", () => {
    const messages = [
      msg("1", "2026-07-05T10:00:00.000Z"),
      msg("2", "2026-07-05T10:01:00.000Z", { sender_user_id: "user-b" }),
    ];
    expect(computeBubbleLayout(messages, 1)).toMatchObject({
      clusterPosition: "single",
      showAvatar: true,
    });
  });

  it("does not cluster system_card messages", () => {
    const messages = [
      msg("1", "2026-07-05T10:00:00.000Z"),
      msg("2", "2026-07-05T10:01:00.000Z", {
        content_type: "system_card",
        content: "公告",
      }),
      msg("3", "2026-07-05T10:02:00.000Z"),
    ];
    expect(computeBubbleLayout(messages, 1)).toMatchObject({
      clusterPosition: "single",
      showAvatar: true,
    });
    expect(computeBubbleLayout(messages, 2)).toMatchObject({
      clusterPosition: "single",
      showAvatar: true,
      tightTop: false,
    });
  });
});

describe("buildImThreadItems", () => {
  it("inserts a date divider when the calendar day changes", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-05T12:00:00"));

    const items = buildImThreadItems([
      msg("1", "2026-07-04T10:00:00"),
      msg("2", "2026-07-05T10:00:00"),
    ]);

    expect(items.map((i) => i.type)).toEqual([
      "date_divider",
      "message",
      "date_divider",
      "message",
    ]);
    expect(items[0]).toMatchObject({ type: "date_divider", label: "昨天" });
    expect(items[2]).toMatchObject({ type: "date_divider", label: "今天" });

    vi.useRealTimers();
  });

  it("starts with a date divider for the first message day", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-05T12:00:00"));

    const items = buildImThreadItems([msg("1", "2026-07-05T10:00:00.000Z")]);
    expect(items[0]).toMatchObject({
      type: "date_divider",
      label: "今天",
    });

    vi.useRealTimers();
  });
});

describe("IM session column", () => {
  it("caps the desktop thread at 40rem, distinct from AI max-w-3xl", () => {
    expect(IM_SESSION_COLUMN_CLASS).toContain("max-w-[40rem]");
    expect(IM_SESSION_COLUMN_CLASS).not.toContain("max-w-3xl");
    expect(IM_BUBBLE_MAX_CLASS).toBe("max-w-[75%]");
  });
});
