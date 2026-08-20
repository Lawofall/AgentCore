import { beforeEach, describe, expect, it } from "vitest";
import {
  FILE_OP_TOOLS,
  autoApproveSiblings,
  isExecutionTool,
  isFileOpTool,
} from "../../services/approvals";
import type { ApprovalRequiredPayload } from "../../types/events";
import {
  type ApprovalView,
  entryToApproval,
  useInteractionStore,
} from "../interactions";

const payload = (
  over: Partial<ApprovalRequiredPayload> = {},
): ApprovalRequiredPayload => ({
  approval_id: "a1",
  conversation_id: "conv-1",
  tool_call_id: "a1",
  tool_name: "file_write",
  arguments: { path: "a.txt" },
  ...over,
});

const store = () => useInteractionStore.getState();

function upsertApproval(p: ApprovalRequiredPayload): void {
  store().upsertRequired({
    kind: "approval",
    conversationId: p.conversation_id,
    messageId: "",
    payload: p as unknown as Record<string, unknown>,
  });
}

function pendingApprovals(conversationId?: string): ApprovalView[] {
  const out: ApprovalView[] = [];
  for (const e of store().byId.values()) {
    if (e.kind !== "approval") continue;
    if (e.status !== "pending" && e.status !== "submitting") continue;
    if (conversationId && e.conversationId !== conversationId) continue;
    out.push(entryToApproval(e));
  }
  return out;
}

beforeEach(() => store().clear());

describe("approval via InteractionStore", () => {
  it("maps a wire payload into a pending card", () => {
    upsertApproval(payload());
    const pending = pendingApprovals();
    expect(pending).toHaveLength(1);
    const p = pending[0];
    expect(p.approvalId).toBe("a1");
    expect(p.toolName).toBe("file_write");
    expect(p.arguments).toEqual({ path: "a.txt" });
    expect(p.resolving).toBe(false);
  });

  it("ignores a re-delivered event for an id already pending", () => {
    upsertApproval(payload());
    upsertApproval(payload({ tool_name: "code_execute" }));
    const pending = pendingApprovals();
    expect(pending).toHaveLength(1);
    expect(pending[0].toolName).toBe("file_write");
  });

  it("removes by id and clears, both idempotent", () => {
    upsertApproval(payload({ approval_id: "a1", tool_call_id: "a1" }));
    upsertApproval(payload({ approval_id: "a2", tool_call_id: "a2" }));
    store().remove("a1");
    expect(pendingApprovals().map((p) => p.approvalId)).toEqual(["a2"]);
    store().remove("a1");
    expect(pendingApprovals()).toHaveLength(1);
    store().clear();
    expect(pendingApprovals()).toHaveLength(0);
  });

  it("toggles submitting via beginSubmit / reopen", () => {
    upsertApproval(payload());
    expect(store().beginSubmit("a1")).toBe(true);
    expect(store().get("a1")?.status).toBe("submitting");
    store().reopen("a1");
    expect(store().get("a1")?.status).toBe("pending");
  });

  it("clears only one conversation's cards, omitting the id wipes all", () => {
    upsertApproval(payload({ approval_id: "a1", conversation_id: "conv-1" }));
    upsertApproval(payload({ approval_id: "a2", conversation_id: "conv-2" }));
    store().clear("conv-1");
    expect(pendingApprovals().map((p) => p.approvalId)).toEqual(["a2"]);
    store().clear();
    expect(pendingApprovals()).toHaveLength(0);
  });
});

const card = (over: Partial<ApprovalView> = {}): ApprovalView => ({
  approvalId: "a1",
  conversationId: "conv-1",
  toolCallId: "a1",
  toolName: "file_write",
  arguments: {},
  resolving: false,
  ...over,
});

describe("autoApproveSiblings (本轮内都允许 batch放行)", () => {
  it("FILE_OP_TOOLS matches backend approval_class_tool_names (文件改动类 ∪ git)", () => {
    expect([...FILE_OP_TOOLS].sort()).toEqual(
      [
        "file_append",
        "file_batch",
        "file_copy",
        "file_delete",
        "file_move",
        "file_write",
        "git",
        "mkdir",
        "str_replace",
      ].sort(),
    );
    expect(isFileOpTool("git")).toBe(true);
    expect(isFileOpTool("code_execute")).toBe(false);
  });

  it("returns the other pending cards for the same tool", () => {
    const siblings = autoApproveSiblings(
      [
        card(),
        card({ approvalId: "a2", toolCallId: "a2" }),
        card({
          approvalId: "a3",
          toolCallId: "a3",
          toolName: "code_execute",
        }),
      ],
      card(),
      "approve_always",
    );
    expect(siblings.map((s) => s.approvalId)).toEqual(["a2"]);
  });

  it("approve_always_files covers file-op siblings only", () => {
    const siblings = autoApproveSiblings(
      [
        card(),
        card({ approvalId: "a2", toolCallId: "a2", toolName: "file_append" }),
        card({
          approvalId: "a3",
          toolCallId: "a3",
          toolName: "code_execute",
        }),
      ],
      card(),
      "approve_always_files",
    );
    expect(siblings.map((s) => s.approvalId)).toEqual(["a2"]);
  });

  it("approve_always_files covers git (aligned with backend approval_class_tool_names)", () => {
    const siblings = autoApproveSiblings(
      [
        card(),
        card({ approvalId: "a2", toolCallId: "a2", toolName: "git" }),
        card({
          approvalId: "a3",
          toolCallId: "a3",
          toolName: "code_execute",
        }),
      ],
      card(),
      "approve_always_files",
    );
    expect(siblings.map((s) => s.approvalId)).toEqual(["a2"]);
  });

  it("approve_always_files skips git push siblings", () => {
    const siblings = autoApproveSiblings(
      [
        card(),
        card({
          approvalId: "a2",
          toolCallId: "a2",
          toolName: "git",
          arguments: { subcommand: "add", paths: ["a.txt"] },
        }),
        card({
          approvalId: "a3",
          toolCallId: "a3",
          toolName: "git",
          arguments: { subcommand: "push", remote: "origin" },
        }),
      ],
      card(),
      "approve_always_files",
    );
    expect(siblings.map((s) => s.approvalId)).toEqual(["a2"]);
  });

  it("approve_always on host skips install_package siblings", () => {
    const siblings = autoApproveSiblings(
      [
        card({
          approvalId: "a1",
          toolCallId: "a1",
          toolName: "host",
          arguments: { action: "shell", command: "Get-Process" },
        }),
        card({
          approvalId: "a2",
          toolCallId: "a2",
          toolName: "host",
          arguments: {
            action: "install_package",
            manager: "winget",
            package_id: "Git.Git",
          },
        }),
      ],
      card({
        approvalId: "a1",
        toolCallId: "a1",
        toolName: "host",
        arguments: { action: "shell", command: "Get-Process" },
      }),
      "approve_always",
    );
    expect(siblings).toEqual([]);
  });

  it("approve_always on git skips push siblings", () => {
    const siblings = autoApproveSiblings(
      [
        card({
          approvalId: "a1",
          toolCallId: "a1",
          toolName: "git",
          arguments: { subcommand: "commit", message: "x" },
        }),
        card({
          approvalId: "a2",
          toolCallId: "a2",
          toolName: "git",
          arguments: { subcommand: "push" },
        }),
      ],
      card({
        approvalId: "a1",
        toolCallId: "a1",
        toolName: "git",
        arguments: { subcommand: "commit", message: "x" },
      }),
      "approve_always",
    );
    expect(siblings).toEqual([]);
  });

  it("skips cards already submitting", () => {
    const siblings = autoApproveSiblings(
      [card(), card({ approvalId: "a2", toolCallId: "a2", resolving: true })],
      card(),
      "approve_always",
    );
    expect(siblings).toEqual([]);
  });

  it("returns empty for a one-shot approve", () => {
    expect(
      autoApproveSiblings(
        [card(), card({ approvalId: "a2", toolCallId: "a2" })],
        card(),
        "approve",
      ),
    ).toEqual([]);
  });
});

describe("isExecutionTool (工具审批 A+B · 主 CTA 偏向 turn grant)", () => {
  it("covers terminal / code_execute / test_run", () => {
    expect(isExecutionTool("terminal")).toBe(true);
    expect(isExecutionTool("code_execute")).toBe(true);
    expect(isExecutionTool("test_run")).toBe(true);
  });

  it("excludes file-op tools", () => {
    expect(isExecutionTool("file_write")).toBe(false);
    expect(isExecutionTool("git")).toBe(false);
    for (const name of FILE_OP_TOOLS) {
      expect(isExecutionTool(name)).toBe(false);
    }
  });
});
