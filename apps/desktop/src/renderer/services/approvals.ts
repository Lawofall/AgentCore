import { ApiError } from "@/services/api";
import {
  isInteractionOrphanedError,
  notifySubmitInteractionResult,
  submitInteraction,
} from "@/services/interactionSubmit";
import {
  type ApprovalView,
  entryToApproval,
  useInteractionStore,
} from "@/stores/interactions";
import type { ApprovalDecision } from "@/types/events";

/**
 * Settle the user's decision over the unified interaction bridge (kind `approval`).
 */
export async function resolveApproval(
  conversationId: string,
  approvalId: string,
  decision: ApprovalDecision,
): Promise<void> {
  await submitInteraction({
    id: approvalId,
    kind: "approval",
    conversationId,
    hotBody: { kind: "approval", decision },
  });
}

/** 本轮内允许所有文件改动 — 对齐后端 ``approval_class_tool_names()``
 * （GRANTABLE ∩ FILESYSTEM ∪ {git}）。 */
export const FILE_OP_TOOLS: ReadonlySet<string> = new Set([
  "file_write",
  "file_append",
  "str_replace",
  "file_delete",
  "file_move",
  "file_copy",
  "mkdir",
  "file_batch",
  "git",
]);

export function isFileOpTool(name: string): boolean {
  return FILE_OP_TOOLS.has(name);
}

/** Structured ``git push`` — never auto-swept by file-class / turn grants. */
export function isGitPushApproval(view: ApprovalView): boolean {
  if (view.toolName !== "git") return false;
  const sub = view.arguments?.subcommand;
  return typeof sub === "string" && sub.trim().toLowerCase() === "push";
}

/** `host(action=install_package)` — 恒确认，对齐 git push；旧名仅历史卡。 */
export function isHostInstallPackageApproval(view: ApprovalView): boolean {
  if (view.toolName === "host_package_install") return true;
  if (view.toolName !== "host") return false;
  const action = view.arguments?.action;
  return (
    typeof action === "string" && action.trim().toLowerCase() === "install_package"
  );
}

export const PER_CALL_TOOLS: ReadonlySet<string> = new Set();

/** Align with backend ``execution_class_tool_names()`` (code / test / terminal + browser_*). */
export const EXECUTION_TOOLS: ReadonlySet<string> = new Set([
  "code_execute",
  "test_run",
  "terminal",
  "browser_navigate",
  "browser_click",
  "browser_type",
  "browser_scroll",
  "browser_snapshot",
  "browser_screenshot",
]);

export function isExecutionTool(name: string): boolean {
  return EXECUTION_TOOLS.has(name) || name.startsWith("browser_");
}

export function supportsTurnGrant(name: string): boolean {
  return !PER_CALL_TOOLS.has(name);
}

export function autoApproveSiblings(
  pending: ApprovalView[],
  approval: ApprovalView,
  decision: ApprovalDecision,
): ApprovalView[] {
  const inScope =
    decision === "approve_always"
      ? (p: ApprovalView) => p.toolName === approval.toolName
      : decision === "approve_always_files"
        ? (p: ApprovalView) => isFileOpTool(p.toolName)
        : () => false;
  return pending.filter(
    (p) =>
      p.approvalId !== approval.approvalId &&
      p.conversationId === approval.conversationId &&
      !p.resolving &&
      inScope(p) &&
      // Remote publish / 本机装包 always needs its own confirm.
      !isGitPushApproval(p) &&
      !isHostInstallPackageApproval(p),
  );
}

function listPendingApprovals(conversationId: string): ApprovalView[] {
  const out: ApprovalView[] = [];
  for (const e of useInteractionStore.getState().byId.values()) {
    if (e.conversationId !== conversationId) continue;
    if (e.kind !== "approval") continue;
    if (e.status !== "pending" && e.status !== "submitting") continue;
    out.push(entryToApproval(e));
  }
  return out;
}

/**
 * Settle one approval card (and, for a turn-scoped grant, its in-scope siblings).
 *
 * 410 / interaction_orphaned → 已失效灰态 (via InteractionStore). Other failures reopen.
 */
export async function decideApproval(
  approval: ApprovalView,
  decision: ApprovalDecision,
): Promise<void> {
  const siblings = autoApproveSiblings(
    listPendingApprovals(approval.conversationId),
    approval,
    decision,
  );

  await Promise.all([
    settleOne(approval, decision),
    ...siblings.map((s) => settleOne(s, "approve")),
  ]);
}

async function settleOne(
  approval: ApprovalView,
  decision: ApprovalDecision,
): Promise<void> {
  const ix = useInteractionStore.getState();
  if (!ix.get(approval.approvalId)) {
    ix.upsertRequired({
      kind: "approval",
      conversationId: approval.conversationId,
      messageId: "",
      payload: {
        approval_id: approval.approvalId,
        conversation_id: approval.conversationId,
        tool_call_id: approval.toolCallId,
        tool_name: approval.toolName,
        arguments: approval.arguments,
      },
    });
  }
  try {
    const result = await submitInteraction({
      id: approval.approvalId,
      kind: "approval",
      conversationId: approval.conversationId,
      hotBody: { kind: "approval", decision },
    });
    if (result !== "ok") {
      notifySubmitInteractionResult(result);
    }
  } catch (err) {
    if (isInteractionOrphanedError(err)) {
      useInteractionStore.getState().markOrphaned(approval.approvalId);
      return;
    }
    if (err instanceof ApiError && err.status === 404) {
      useInteractionStore
        .getState()
        .markSettledByReceipt({ kind: "approval", id: approval.approvalId });
      return;
    }
    throw err;
  }
}
