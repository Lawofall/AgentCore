import {
  type ResolveInteractionBody,
  resolveInteraction,
} from "@/api/interaction";
import {
  markLocalSettlement,
  noteRemoteSettlementFromReceipt,
} from "@/lib/remoteSettlement";
// The interactive pause card — the actionable surface for a turn blocked on the user
// (前端技术与架构 §七 · 交互式暂停放行). The conformance-checked fold computes
// `interactions[]`; this turns an approval leaf into buttons that POST the decision to
// the live stream (api/interaction.ts), which resumes the SAME SSE.
//
// 挂起即收口 (②, Phase 3): only hot-path cards resolve live in-stream. A CEO checkpoint
// (ask_user) / plan_review / team_preview finalizes the turn and is continued via the
// durable ResumeCard (the single cold resume path).
//
// This is mobile's own UI (cross-platform-frontend.mdc: zero shared business components).
import type { ApprovalDecision } from "@agentcore/contract-types";
import type { ProjectedInteraction } from "@agentcore/protocol-conformance";
import { type ReactNode, useState } from "react";

type ApprovalPending = Extract<ProjectedInteraction, { kind: "approval" }>;

/** Friendly zh labels for the GRANTABLE built-ins; falls back to the raw name. */
const TOOL_LABELS: Record<string, string> = {
  file_write: "写入文件",
  file_append: "追加文件",
  str_replace: "修改文件",
  file_delete: "删除文件",
  file_move: "移动文件",
  code_execute: "执行代码",
  delete_folder: "删除文件夹",
  host: "本机 Host",
};

/** 本轮内所有文件改动 — 对齐后端 ``approval_class_tool_names()``
 * （文件改动五工具 ∪ {git}）。 */
export const FILE_OP_TOOLS: ReadonlySet<string> = new Set([
  "file_write",
  "file_append",
  "str_replace",
  "file_delete",
  "file_move",
  "git",
]);

/** Tools whose card omits「本轮都允许」— mirrors backend per_call_tool_names(). */
const PER_CALL_TOOLS: ReadonlySet<string> = new Set();

function hostPrimaryArg(args: Record<string, unknown>): string | null {
  const action = typeof args.action === "string" ? args.action.trim() : "";
  if (!action) return null;
  if (action === "shell") {
    const cmd = typeof args.command === "string" ? args.command.trim() : "";
    return cmd ? `shell ${cmd}` : "shell";
  }
  if (action === "install_package") {
    const manager =
      typeof args.manager === "string" && args.manager.trim()
        ? args.manager.trim()
        : "";
    const pkg =
      typeof args.package_id === "string" && args.package_id.trim()
        ? args.package_id.trim()
        : "";
    const cask = args.cask === true ? " (cask)" : "";
    if (manager && pkg) return `install_package ${manager} ${pkg}${cask}`;
    return pkg || manager || "install_package";
  }
  if (action === "open_settings") {
    const panel = typeof args.panel === "string" ? args.panel.trim() : "";
    return panel ? `open_settings ${panel}` : "open_settings";
  }
  if (action === "set_audio") {
    const name =
      typeof args.device_name === "string" && args.device_name.trim()
        ? args.device_name.trim()
        : typeof args.device_id === "string" && args.device_id.trim()
          ? args.device_id.trim()
          : "";
    return name ? `set_audio ${name}` : "set_audio";
  }
  if (action === "restart_service") {
    const service =
      typeof args.service === "string" && args.service.trim()
        ? args.service.trim()
        : "";
    return service ? `restart_service ${service}` : "restart_service";
  }
  return action;
}

function primaryArg(
  args: Record<string, unknown>,
  toolName?: string,
): string | null {
  if (toolName === "host") return hostPrimaryArg(args);
  // delete_folder 只带 folder_id；``folder_name`` 由后端按权威名册补，
  // 不是模型自报——一串 UUID 用户审不了。
  for (const key of ["folder_name", "path", "file_path", "command", "code"]) {
    const v = args[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return null;
}

export function PauseCard({
  pending,
  conversationId,
  onResolved,
}: {
  pending: ApprovalPending;
  conversationId: string;
  onResolved?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Orphaned / non-pending: silent dismiss (no tombstone card).
  if (pending.status !== "pending") return null;

  async function submit(body: ResolveInteractionBody) {
    if (busy) return;
    setBusy(true);
    setErr(null);
    // 登记在 POST 之前：抢先回来的 `approval_resolved` 才认得出是自己点的（B2 · 验收 5）。
    markLocalSettlement(pending.id);
    try {
      const outcome = await resolveInteraction(
        conversationId,
        pending.id,
        body,
      );
      if (outcome === "already_processed") {
        noteRemoteSettlementFromReceipt({
          interactionId: pending.id,
          conversationId,
          kind: "approval",
        });
      }
      onResolved?.();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "放行失败");
      setBusy(false);
    }
  }

  return (
    <div className="pause pause--budget">
      <ApprovalBody
        pending={pending}
        busy={busy}
        onDecide={(decision) => void submit({ kind: "approval", decision })}
      />
      {busy && <div className="pause-busy">处理中…</div>}
      {err && <div className="error pause-err">{err}</div>}
    </div>
  );
}

/** True fuse / destructive FORCE — hide turn grants; keep honest fuse copy. */
function isForceOneShot(args: Record<string, unknown>): boolean {
  return args.force_one_shot === true;
}

/** Sensitive-path ASK read — turn grant OK; no fuse boilerplate. */
function isSensitivePathReadAsk(args: Record<string, unknown>): boolean {
  if (isForceOneShot(args)) return false;
  if (args.rule_id === "sensitive.path_read_ask") return true;
  // Backend may also stamp allow_turn_grant (desktop parity).
  return args.allow_turn_grant === true;
}

function ApprovalBody({
  pending,
  busy,
  onDecide,
}: {
  pending: ApprovalPending;
  busy: boolean;
  onDecide: (decision: ApprovalDecision) => void;
}) {
  const headline = primaryArg(pending.arguments, pending.toolName);
  const label = TOOL_LABELS[pending.toolName] ?? pending.toolName;
  const circuitBreakerHint =
    typeof pending.arguments.circuit_breaker_hint === "string"
      ? pending.arguments.circuit_breaker_hint.trim()
      : "";
  const forceOneShot = isForceOneShot(pending.arguments);
  const sensitiveReadAsk = isSensitivePathReadAsk(pending.arguments);
  // Machine-readable flags only — never branch fuse UX on hint presence alone.
  // Copy aligns with desktop ApprovalPrompt (parity).
  const hintLine = forceOneShot
    ? `安全熔断升格审批（启发式兜底，并非完整拦截）${
        circuitBreakerHint ? `：${circuitBreakerHint}` : ""
      }`
    : sensitiveReadAsk
      ? `敏感路径读升格审批${
          circuitBreakerHint ? `：${circuitBreakerHint}` : ""
        }`
      : null;
  const showTurnGrants = !forceOneShot;
  return (
    <>
      <div className="pause-scroll">
        <div className="pause-title">Agent 请求执行 · {label}</div>
        {headline && <div className="pause-arg">{headline}</div>}
        {hintLine ? <div className="pause-hint">{hintLine}</div> : null}
      </div>
      <div className="pause-actions">
        <Btn tone="primary" disabled={busy} onClick={() => onDecide("approve")}>
          允许一次
        </Btn>
        {showTurnGrants && !PER_CALL_TOOLS.has(pending.toolName) && (
          <Btn
            tone="neutral"
            disabled={busy}
            onClick={() => onDecide("approve_always")}
          >
            本轮都允许
          </Btn>
        )}
        {showTurnGrants && FILE_OP_TOOLS.has(pending.toolName) && (
          <Btn
            tone="neutral"
            disabled={busy}
            onClick={() => onDecide("approve_always_files")}
          >
            本轮内所有文件改动
          </Btn>
        )}
        <Btn tone="danger" disabled={busy} onClick={() => onDecide("deny")}>
          拒绝
        </Btn>
      </div>
    </>
  );
}

function Btn({
  tone,
  disabled,
  onClick,
  children,
}: {
  tone: "primary" | "neutral" | "danger";
  disabled: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`pause-btn pause-btn-${tone}`}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
