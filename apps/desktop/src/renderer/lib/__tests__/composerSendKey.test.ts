import { isComposerSubmitKey, occupiedQueueHint } from "@/lib/composerSendKey";
import { describe, expect, it } from "vitest";

function key(
  over: Partial<{
    key: string;
    shiftKey: boolean;
    ctrlKey: boolean;
    metaKey: boolean;
  }> = {},
) {
  return {
    key: "Enter",
    shiftKey: false,
    ctrlKey: false,
    metaKey: false,
    ...over,
  };
}

describe("isComposerSubmitKey", () => {
  it("always submits on Ctrl/Cmd+Enter", () => {
    expect(isComposerSubmitKey(key({ ctrlKey: true }), true)).toBe("mod");
    expect(isComposerSubmitKey(key({ metaKey: true }), false)).toBe("mod");
  });

  it("bare Enter submits only when send-on-enter is on", () => {
    expect(isComposerSubmitKey(key(), true)).toBe("bare");
    expect(isComposerSubmitKey(key(), false)).toBeNull();
  });

  it("Shift+Enter never submits", () => {
    expect(isComposerSubmitKey(key({ shiftKey: true }), true)).toBeNull();
    expect(isComposerSubmitKey(key({ shiftKey: true }), false)).toBeNull();
  });

  it("ignores non-Enter keys", () => {
    expect(isComposerSubmitKey(key({ key: "Tab" }), true)).toBeNull();
  });
});

describe("occupiedQueueHint", () => {
  it("names Enter only when bare Enter submits", () => {
    expect(occupiedQueueHint(true)).toBe(
      "排队至本回合结束后发送（Enter）；Ctrl/Cmd+Enter 送进当前回合",
    );
    expect(occupiedQueueHint(false)).toBe(
      "排队至本回合结束后发送；Ctrl/Cmd+Enter 送进当前回合",
    );
  });
});
