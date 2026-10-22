import { shouldCenterDraftComposer } from "@/lib/onboarding";
import { describe, expect, it } from "vitest";

describe("shouldCenterDraftComposer", () => {
  it("centers an empty draft (keyless included — no access gate)", () => {
    expect(
      shouldCenterDraftComposer({
        isDraft: true,
        hasMessages: false,
      }),
    ).toBe(true);
  });

  it("never centers once messages exist", () => {
    expect(
      shouldCenterDraftComposer({
        isDraft: true,
        hasMessages: true,
      }),
    ).toBe(false);
  });

  it("never centers a persisted conversation still loading history", () => {
    // 回归护栏：切换到已有对话会先出现「有 conversationId(isDraft=false) 但消息尚未
    // 异步加载完」的空窗口。若在此居中，输入框会「弹到中间、加载完再飞回底栏」= 跳动。
    // 未知 messageCount（列表未命中）的已落库对话一律底栏。
    expect(
      shouldCenterDraftComposer({
        isDraft: false,
        hasMessages: false,
      }),
    ).toBe(false);
  });

  it("centers a persisted conversation known to be empty (demo-tape 绑定空会话)", () => {
    // 演示磁带 prepare 建的是已落库(isDraft=false)、0 消息的空会话；回放开始时应显示
    // 真实产品的居中欢迎卡片，而非直接底栏。messageCount===0 是「确定为空」的可靠信号。
    expect(
      shouldCenterDraftComposer({
        isDraft: false,
        hasMessages: false,
        knownEmptyPersisted: true,
      }),
    ).toBe(true);
  });
});
