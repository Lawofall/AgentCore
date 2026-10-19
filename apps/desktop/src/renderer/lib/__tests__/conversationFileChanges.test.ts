import {
  conversationChangeCounts,
  conversationHasFileArtifacts,
  conversationHasRestorableEntry,
  shouldBounceChangesTabToWorkspace,
  shouldIncludeChangesTurn,
  shouldPinChangesTab,
  shouldShowComposerChangesHint,
} from "@/lib/conversationFileChanges";
import type { Message } from "@/stores/conversation/types";
import { CHANGES_TAB_ID, WORKSPACE_TAB_ID } from "@/stores/sidePanel/types";
import type { ProcessStep } from "@/types/events";
import { describe, expect, it } from "vitest";

function toolStep(
  tool_name: string,
  args: Record<string, unknown>,
): ProcessStep {
  return {
    kind: "tool",
    id: `t-${tool_name}`,
    tool_name,
    arguments: args,
    result: null,
    status: "success",
  };
}

function msg(
  partial: Partial<Message> & Pick<Message, "id" | "role">,
): Message {
  return {
    content: "",
    createdAt: new Date().toISOString(),
    executionId: null,
    isStreaming: false,
    ...partial,
  };
}

describe("conversationHasFileArtifacts", () => {
  it("is false when there are no assistant file ops", () => {
    expect(
      conversationHasFileArtifacts(
        [
          msg({ id: "u1", role: "user", content: "hi" }),
          msg({ id: "a1", role: "assistant", content: "ok" }),
        ],
        {},
      ),
    ).toBe(false);
  });

  it("is true when process has a successful file write", () => {
    expect(
      conversationHasFileArtifacts(
        [
          msg({
            id: "a1",
            role: "assistant",
            process: [toolStep("write", { file_path: "a.ts", content: "x" })],
          }),
        ],
        {},
      ),
    ).toBe(true);
  });
});

describe("conversationChangeCounts", () => {
  it("counts last op per path", () => {
    expect(
      conversationChangeCounts(
        [
          msg({
            id: "a1",
            role: "assistant",
            process: [
              toolStep("write", { file_path: "a.ts", content: "x" }),
              toolStep("edit", {
                file_path: "b.ts",
                old_string: "a",
                new_string: "b",
              }),
              toolStep("file_delete", { path: "c.ts" }),
            ],
          }),
        ],
        {},
      ),
    ).toEqual({ added: 1, modified: 1, deleted: 1 });
  });

  it("later op on the same path wins", () => {
    expect(
      conversationChangeCounts(
        [
          msg({
            id: "a1",
            role: "assistant",
            process: [toolStep("write", { file_path: "a.ts", content: "x" })],
          }),
          msg({
            id: "a2",
            role: "assistant",
            process: [toolStep("file_delete", { path: "a.ts" })],
          }),
        ],
        {},
      ),
    ).toEqual({ added: 0, modified: 0, deleted: 1 });
  });
});

describe("conversationHasRestorableEntry (P0c)", () => {
  it("is true when only a Local baseline exists (no file_*)", () => {
    expect(
      conversationHasRestorableEntry(
        [
          msg({ id: "u1", role: "user", content: "hi" }),
          msg({ id: "a1", role: "assistant", content: "ran script" }),
        ],
        {},
        new Set(["a1"]),
      ),
    ).toBe(true);
  });

  it("is false when baseline ids do not match this conversation", () => {
    expect(
      conversationHasRestorableEntry(
        [msg({ id: "a1", role: "assistant", content: "ok" })],
        {},
        new Set(["other-turn"]),
      ),
    ).toBe(false);
  });
});

describe("shouldIncludeChangesTurn (P0c)", () => {
  it("includes baseline-only turns", () => {
    expect(
      shouldIncludeChangesTurn({
        artifactsLength: 0,
        messageId: "m1",
        baselineMessageIds: new Set(["m1"]),
        focusMessageId: null,
      }),
    ).toBe(true);
  });

  it("still includes file_* turns without baseline", () => {
    expect(
      shouldIncludeChangesTurn({
        artifactsLength: 2,
        messageId: "m1",
        baselineMessageIds: new Set(),
        focusMessageId: null,
      }),
    ).toBe(true);
  });

  it("skips turns with neither artifacts nor baseline nor focus", () => {
    expect(
      shouldIncludeChangesTurn({
        artifactsLength: 0,
        messageId: "m1",
        baselineMessageIds: new Set(["other"]),
        focusMessageId: null,
      }),
    ).toBe(false);
  });
});

const pinBase = {
  conversationId: "c1" as string | null,
  changesOpen: false,
  isChangesFloating: false,
};

describe("shouldPinChangesTab (P0c · 按需)", () => {
  it("is false for drafts even when open or floating", () => {
    expect(
      shouldPinChangesTab({
        conversationId: null,
        changesOpen: true,
        isChangesFloating: true,
      }),
    ).toBe(false);
  });

  it("does not pin on restorable alone", () => {
    expect(shouldPinChangesTab({ ...pinBase, changesOpen: false })).toBe(false);
  });

  it("pins when the user opened 改动 (showChanges / + menu)", () => {
    expect(shouldPinChangesTab({ ...pinBase, changesOpen: true })).toBe(true);
  });

  it("pins while the tab is floating", () => {
    expect(shouldPinChangesTab({ ...pinBase, isChangesFloating: true })).toBe(
      true,
    );
  });

  it("is false when nothing supports the tab", () => {
    expect(shouldPinChangesTab(pinBase)).toBe(false);
  });
});

describe("shouldShowComposerChangesHint", () => {
  const hintBase = {
    conversationId: "c1" as string | null,
    hasRestorable: true,
    changesOpen: false,
    isChangesFloating: false,
  };

  it("lights when this conversation has restorable changes and 改动 is closed", () => {
    expect(shouldShowComposerChangesHint(hintBase)).toBe(true);
  });

  it("stays off for drafts", () => {
    expect(
      shouldShowComposerChangesHint({ ...hintBase, conversationId: null }),
    ).toBe(false);
  });

  it("stays off when nothing is restorable", () => {
    expect(
      shouldShowComposerChangesHint({ ...hintBase, hasRestorable: false }),
    ).toBe(false);
  });

  it("hides once 改动 is already open", () => {
    expect(
      shouldShowComposerChangesHint({ ...hintBase, changesOpen: true }),
    ).toBe(false);
  });

  it("hides while 改动 is floating", () => {
    expect(
      shouldShowComposerChangesHint({ ...hintBase, isChangesFloating: true }),
    ).toBe(false);
  });
});

describe("shouldBounceChangesTabToWorkspace", () => {
  it("bounces when dock is still on 改动 but tab was closed", () => {
    expect(
      shouldBounceChangesTabToWorkspace({
        activeTabId: CHANGES_TAB_ID,
        changesOpen: false,
      }),
    ).toBe(true);
  });

  it("does not bounce when 改动 is still open", () => {
    expect(
      shouldBounceChangesTabToWorkspace({
        activeTabId: CHANGES_TAB_ID,
        changesOpen: true,
      }),
    ).toBe(false);
  });

  it("does not bounce when dock is not on 改动", () => {
    expect(
      shouldBounceChangesTabToWorkspace({
        activeTabId: WORKSPACE_TAB_ID,
        changesOpen: false,
      }),
    ).toBe(false);
  });
});
