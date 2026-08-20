import { MANUAL_HELP, ManualHelpLink } from "@/components/ManualHelpLink";
import { CodeBlock } from "@/components/chat/CodeBlock";
import {
  codeExecuteLanguage,
  deriveCodeExecuteRiskTags,
  fencedCodeMarkdown,
  isPreviewTruncated,
} from "@/components/chat/codeExecuteApproval";
import { toolLabelZh } from "@/components/chat/toolLabelsZh";
import { Badge, Button, DecisionCard, DecisionCardIcon } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  getConversations,
  patchConversationCache,
} from "@/hooks/useConversations";
import { notifyError } from "@/lib/toast";
import { cn } from "@/lib/utils";
import {
  decideApproval,
  isExecutionTool,
  isFileOpTool,
  supportsTurnGrant,
} from "@/services/approvals";
import {
  type PermissionAxes,
  matchRecipe,
  recipeToAxes,
  setConversationPermissionAxes,
} from "@/services/permissionAxes";
import { useConversationStore } from "@/stores/conversation";
import {
  type ApprovalView,
  isToolGranted,
  useInteractionStore,
  usePendingApprovals,
} from "@/stores/interactions";
import { usePermissionChangeStore } from "@/stores/permissionChanges";
import type { ApprovalDecision } from "@/types/events";
import {
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  FileCheck,
  Loader2,
  ShieldAlert,
  X,
} from "lucide-react";
import { type ComponentPropsWithoutRef, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";

const HIGHLIGHT_PLUGINS: ComponentPropsWithoutRef<
  typeof ReactMarkdown
>["rehypePlugins"] = [[rehypeHighlight, { ignoreMissing: true }]];

/** Consecutive same-tool approval prompts before nudging full_trust. */
const FULL_TRUST_HINT_AFTER = 3;

/**
 * 点「本轮内…」之前必须先知道批出去的是多大范围：一个回合能跑几十次工具、派出多名队员，
 * 而被覆盖的调用之后不会再弹卡（痕迹在过程线的 ApprovalTrace 上）。此刻用户在等放行，
 * 所以只给一句——不是一堵字墙，也不是二次确认。
 */
const TURN_GRANT_SCOPE_NOTICE =
  "「本轮内」= 到这次回答结束前同类操作都不再问你，队员发起的也算；一个回合可能有几十次调用。";
/** 文件类授权比按钮字面更宽（对齐后端 file-class：含 git 写入）。 */
const FILE_CLASS_SCOPE_NOTICE =
  "「所有文件改动」含新建 / 改写 / 删除 / 移动与 git 写入。";

/** Gate-injected meta on ``approval.arguments`` — not tool args; strip from card preview. */
const APPROVAL_GATE_META_KEYS = new Set([
  "circuit_breaker_hint",
  "force_one_shot",
  "rule_id",
  "allow_turn_grant",
]);

/**
 * Machine-readable track for FORCE_APPROVAL cards.
 * Do **not** infer fuse vs sensitive-read from ``circuit_breaker_hint`` alone.
 */
function approvalEscalationTrack(args: Record<string, unknown>): {
  forceOneShot: boolean;
  sensitivePathReadAsk: boolean;
  hint: string;
} {
  const forceOneShot = args.force_one_shot === true;
  const ruleId = typeof args.rule_id === "string" ? args.rule_id.trim() : "";
  const allowTurnGrant = args.allow_turn_grant === true;
  const hint =
    typeof args.circuit_breaker_hint === "string"
      ? args.circuit_breaker_hint.trim()
      : "";
  return {
    forceOneShot,
    sensitivePathReadAsk:
      !forceOneShot && (ruleId === "sensitive.path_read_ask" || allowTurnGrant),
    hint,
  };
}

function isApprovalGateMetaKey(key: string): boolean {
  return APPROVAL_GATE_META_KEYS.has(key);
}

function batchOpLine(item: Record<string, unknown>): string {
  const op = String(item.op ?? "").trim();
  if (op === "move")
    return `move ${item.source ?? ""} → ${item.destination ?? ""}`;
  if (op === "copy")
    return `copy ${item.source ?? ""} → ${item.destination ?? ""}`;
  if (op === "delete") {
    const perm = item.permanent ? " (永久)" : "";
    return `delete ${item.path ?? ""}${perm}`;
  }
  if (op === "mkdir") return `mkdir ${item.path ?? ""}`;
  return JSON.stringify(item);
}

function truncateSnippet(text: string, max = 48): string {
  const t = text.trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max)}…`;
}

/** Format `paths` array for git approval headlines. */
function gitPathsSnippet(args: Record<string, unknown>): string {
  const raw = args.paths;
  if (!Array.isArray(raw)) return "";
  const paths = raw
    .filter((p): p is string => typeof p === "string" && p.trim().length > 0)
    .map((p) => p.trim());
  if (paths.length === 0) return "";
  return truncateSnippet(paths.join(", "));
}

function gitRemoteName(args: Record<string, unknown>): string {
  return typeof args.remote === "string" && args.remote.trim()
    ? args.remote.trim()
    : "origin";
}

/** Readable headline for structured `git` tool (subcommand + key args). */
function gitPrimaryArg(args: Record<string, unknown>): string | null {
  const sub = typeof args.subcommand === "string" ? args.subcommand.trim() : "";
  if (!sub) return null;
  if (sub === "push") {
    return `push → ${gitRemoteName(args)}`;
  }
  if (sub === "pull") {
    return `pull ← ${gitRemoteName(args)}`;
  }
  if (sub === "fetch") {
    return `fetch ← ${gitRemoteName(args)}`;
  }
  if (sub === "commit") {
    const message = typeof args.message === "string" ? args.message.trim() : "";
    return message ? `commit ${truncateSnippet(message)}` : "commit";
  }
  if (sub === "branch" || sub === "checkout") {
    const branch = typeof args.branch === "string" ? args.branch.trim() : "";
    return branch ? `${sub} ${branch}` : sub;
  }
  if (sub === "add") {
    const paths = gitPathsSnippet(args);
    return paths ? `add ${paths}` : "add";
  }
  if (sub === "show") {
    const ref =
      typeof args.ref === "string"
        ? args.ref.trim()
        : typeof args.revision === "string"
          ? args.revision.trim()
          : "";
    if (ref) return `show ${truncateSnippet(ref)}`;
    const paths = gitPathsSnippet(args);
    return paths ? `show ${paths}` : "show";
  }
  if (sub === "blame") {
    const paths = gitPathsSnippet(args);
    return paths ? `blame ${paths}` : "blame";
  }
  if (sub === "stash") {
    const action =
      typeof args.action === "string" && args.action.trim()
        ? args.action.trim()
        : "list";
    return `stash ${action}`;
  }
  if (sub === "merge" || sub === "rebase") {
    const ref =
      typeof args.ref === "string" && args.ref.trim()
        ? args.ref.trim()
        : typeof args.branch === "string" && args.branch.trim()
          ? args.branch.trim()
          : "";
    return ref ? `${sub} ${truncateSnippet(ref)}` : sub;
  }
  if (sub === "cherry-pick" || sub === "cherry_pick") {
    const ref =
      typeof args.ref === "string" && args.ref.trim()
        ? args.ref.trim()
        : typeof args.object === "string" && args.object.trim()
          ? args.object.trim()
          : typeof args.commit === "string" && args.commit.trim()
            ? args.commit.trim()
            : "";
    return ref ? `cherry-pick ${truncateSnippet(ref)}` : "cherry-pick";
  }
  if (sub === "tag") {
    const action =
      typeof args.action === "string" && args.action.trim()
        ? args.action.trim()
        : "list";
    const name =
      typeof args.name === "string" && args.name.trim() ? args.name.trim() : "";
    if (action === "create" || action === "add") {
      return name ? `tag ${name}` : "tag create";
    }
    return action === "list" ? "tag list" : `tag ${action}`;
  }
  if (sub === "remote") {
    const action =
      typeof args.action === "string" && args.action.trim()
        ? args.action.trim()
        : "list";
    const name =
      typeof args.name === "string" && args.name.trim() ? args.name.trim() : "";
    if (action === "add") {
      return name ? `remote add ${name}` : "remote add";
    }
    return action === "list" || action === "-v"
      ? "remote list"
      : `remote ${action}`;
  }
  if (sub === "create_pr") {
    const title =
      typeof args.title === "string" && args.title.trim()
        ? truncateSnippet(args.title.trim())
        : "";
    const head =
      typeof args.head === "string" && args.head.trim() ? args.head.trim() : "";
    const base =
      typeof args.base === "string" && args.base.trim() ? args.base.trim() : "";
    const remote = gitRemoteName(args);
    const arrow = head && base ? `${head} → ${base}` : head || base || "";
    const bits = [
      title ? `create_pr ${title}` : "create_pr",
      arrow,
      remote !== "origin" ? `@ ${remote}` : "",
    ].filter(Boolean);
    return bits.join(" · ");
  }
  return sub;
}

function hostPackageSnippet(args: Record<string, unknown>): string | null {
  const manager =
    typeof args.manager === "string" && args.manager.trim()
      ? args.manager.trim()
      : "";
  const pkg =
    typeof args.package_id === "string" && args.package_id.trim()
      ? args.package_id.trim()
      : "";
  const cask = args.cask === true ? " (cask)" : "";
  if (manager && pkg) return `${manager} ${pkg}${cask}`;
  return pkg || manager || null;
}

/** Readable headline for structured `host` tool (action + key args; 同构 git). */
function hostPrimaryArg(args: Record<string, unknown>): string | null {
  const action = typeof args.action === "string" ? args.action.trim() : "";
  if (!action) return hostPackageSnippet(args);
  if (action === "shell") {
    const cmd = typeof args.command === "string" ? args.command.trim() : "";
    return cmd ? `shell ${truncateSnippet(cmd)}` : "shell";
  }
  if (action === "install_package") {
    const pkg = hostPackageSnippet(args);
    return pkg ? `install_package ${pkg}` : "install_package";
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
    return name ? `set_audio ${truncateSnippet(name)}` : "set_audio";
  }
  if (action === "restart_service") {
    const service =
      typeof args.service === "string" && args.service.trim()
        ? args.service.trim()
        : "";
    return service ? `restart_service ${service}` : "restart_service";
  }
  if (action === "os_log") {
    const source =
      typeof args.source === "string" && args.source.trim()
        ? args.source.trim()
        : "";
    return source ? `os_log ${source}` : "os_log";
  }
  if (action === "status") {
    const raw = args.facets;
    if (Array.isArray(raw)) {
      const facets = raw
        .filter(
          (f): f is string => typeof f === "string" && f.trim().length > 0,
        )
        .map((f) => f.trim());
      if (facets.length > 0) return `status ${facets.join(", ")}`;
    }
    return "status";
  }
  return action;
}

function primaryArg(
  toolName: string,
  args: Record<string, unknown>,
): string | null {
  if (toolName === "git") return gitPrimaryArg(args);
  if (toolName === "browser") {
    const action = typeof args.action === "string" ? args.action.trim() : "";
    const url = typeof args.url === "string" ? args.url.trim() : "";
    const ref = typeof args.ref === "string" ? args.ref.trim() : "";
    if (action === "navigate") {
      return url ? `navigate ${truncateSnippet(url)}` : "navigate";
    }
    if (action === "click") return ref ? `click ${ref}` : "click";
    if (action === "type") {
      const text = typeof args.text === "string" ? args.text.trim() : "";
      return text
        ? `type ${truncateSnippet(text)}`
        : ref
          ? `type ${ref}`
          : "type";
    }
    if (action === "scroll") {
      const dy = args.dy;
      return typeof dy === "number" ? `scroll ${dy}px` : "scroll";
    }
    return action || url || null;
  }
  if (toolName === "host") return hostPrimaryArg(args);
  if (toolName === "host_package_install") {
    return hostPackageSnippet(args);
  }
  if (toolName === "delete_folder") {
    // ``folder_name`` is resolved server-side from the roster (never model-supplied)
    // — a bare folder_id UUID is unauditable.
    const name =
      typeof args.folder_name === "string" ? args.folder_name.trim() : "";
    const id = typeof args.folder_id === "string" ? args.folder_id.trim() : "";
    if (name) return `${name}${id ? ` · ${id}` : ""}`;
    return id || null;
  }
  if (toolName === "file_batch") {
    const ops = args.operations;
    if (Array.isArray(ops)) return `本次共 ${ops.length} 项`;
  }
  if (toolName === "code_execute") {
    const purpose = args.purpose;
    if (typeof purpose === "string" && purpose.trim()) return purpose.trim();
  }
  if (toolName === "terminal") {
    const cmd = args.command;
    if (typeof cmd === "string" && cmd.trim()) return cmd.trim();
  }
  for (const key of [
    "path",
    "file_path",
    "source",
    "destination",
    "command",
    "code",
    "title",
    "body",
  ]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

/** Count approval cards (any status except orphaned) for a tool this conversation. */
function countToolApprovals(conversationId: string, toolName: string): number {
  let n = 0;
  for (const e of useInteractionStore.getState().byId.values()) {
    if (e.conversationId !== conversationId) continue;
    if (e.kind !== "approval") continue;
    if (e.status === "orphaned") continue;
    if (String(e.payload.tool_name ?? "") !== toolName) continue;
    n += 1;
  }
  return n;
}

/**
 * Pending tool-approval surface — composer-dock strip（ChatView 决策区 / 底栏一体）.
 * Visually fuses with MessageInput when ``attached`` (Chat bottom bar).
 */
export function ApprovalPrompt({
  attached = false,
}: {
  /** True when stacked flush above the chat composer (同底栏一体). */
  attached?: boolean;
}) {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const pending = usePendingApprovals(conversationId);
  const visible = pending.filter(
    (p) => conversationId != null && !isToolGranted(conversationId, p.toolName),
  );
  if (visible.length === 0) return null;

  return (
    <div
      className={cn("space-y-2", attached ? "px-0" : "mx-4 mb-2")}
      data-approval-dock={attached ? "composer" : "panel"}
    >
      {visible.map((approval) => (
        <ApprovalCard
          key={approval.approvalId}
          approval={approval}
          attached={attached}
        />
      ))}
    </div>
  );
}

/** 单张工具审批卡。可选 `onDecide` 供手册等纯演示覆盖默认提交路径。 */
export function ApprovalCard({
  approval,
  onDecide: onDecideProp,
  attached = false,
}: {
  approval: ApprovalView;
  onDecide?: (decision: ApprovalDecision) => void;
  attached?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [clicked, setClicked] = useState<ApprovalDecision | null>(null);
  const [trustBusy, setTrustBusy] = useState(false);
  const [axesOverride, setAxesOverride] = useState<PermissionAxes | null>(null);
  const axes =
    axesOverride ??
    getConversations().find((c) => c.id === approval.conversationId)
      ?.permissionAxes;
  const recipe = axes ? matchRecipe(axes) : "custom";

  const isCodeExecute = approval.toolName === "code_execute";
  const isFileBatch = approval.toolName === "file_batch";
  const isExecution = isExecutionTool(approval.toolName);
  const busy = approval.resolving;
  const isFileOp = isFileOpTool(approval.toolName);
  const {
    forceOneShot,
    sensitivePathReadAsk,
    hint: escalationHint,
  } = approvalEscalationTrack(approval.arguments);
  /** True fuse: no turn-scope grants (approve_always / approve_always_files). */
  const showTurnGrantButtons = !forceOneShot;
  const preferTurnGrant =
    showTurnGrantButtons && isExecution && supportsTurnGrant(approval.toolName);
  /** 有「本轮内…」按钮才说范围；熔断一次性卡没有轮内授权，不该出现「本轮」字样。 */
  const showScopeNotice =
    showTurnGrantButtons && (supportsTurnGrant(approval.toolName) || isFileOp);
  const headline = primaryArg(approval.toolName, approval.arguments);
  const argEntries = Object.entries(approval.arguments).filter(
    ([key]) => !isApprovalGateMetaKey(key),
  );

  const sameToolCount = useMemo(
    () => countToolApprovals(approval.conversationId, approval.toolName),
    [approval.conversationId, approval.toolName],
  );
  const showManagedHint =
    sameToolCount >= FULL_TRUST_HINT_AFTER &&
    recipe !== "managed" &&
    !forceOneShot &&
    !sensitivePathReadAsk;

  const batchOps = useMemo(() => {
    if (!isFileBatch) return [];
    const ops = approval.arguments.operations;
    if (!Array.isArray(ops)) return [];
    return ops.filter(
      (item): item is Record<string, unknown> =>
        item != null && typeof item === "object" && !Array.isArray(item),
    );
  }, [approval.arguments.operations, isFileBatch]);

  const codeText =
    isCodeExecute && typeof approval.arguments.code === "string"
      ? approval.arguments.code
      : null;
  const riskTags = useMemo(
    () => (codeText ? deriveCodeExecuteRiskTags(codeText) : []),
    [codeText],
  );
  const codeTruncated = codeText != null && isPreviewTruncated(codeText);
  const otherArgs = useMemo(() => {
    if (!isCodeExecute) return approval.arguments;
    return Object.fromEntries(
      Object.entries(approval.arguments).filter(
        ([key]) =>
          key !== "code" && key !== "purpose" && !isApprovalGateMetaKey(key),
      ),
    );
  }, [approval.arguments, isCodeExecute]);
  const displayArgs = useMemo(() => {
    if (isCodeExecute) return otherArgs;
    if (isFileBatch) {
      return Object.fromEntries(
        Object.entries(approval.arguments).filter(
          ([key]) => !isApprovalGateMetaKey(key) && key !== "operations",
        ),
      );
    }
    return Object.fromEntries(
      Object.entries(approval.arguments).filter(
        ([key]) => !isApprovalGateMetaKey(key),
      ),
    );
  }, [approval.arguments, isCodeExecute, isFileBatch, otherArgs]);

  const onDecide = (decision: ApprovalDecision) => {
    setClicked(decision);
    if (onDecideProp) {
      onDecideProp(decision);
      return;
    }
    void decideApproval(approval, decision).catch((err) => {
      notifyError(err, "操作失败");
    });
  };

  const switchManaged = () => {
    if (trustBusy || !approval.conversationId) return;
    if (
      !window.confirm(
        "切换到「托管」后，执行类（代码/终端/浏览器等）与桌面提醒将免审；本机 Host 本会话信任。确定继续？",
      )
    ) {
      return;
    }
    setTrustBusy(true);
    const next = recipeToAxes("managed");
    void setConversationPermissionAxes(approval.conversationId, next)
      .then((saved) => {
        patchConversationCache(approval.conversationId, {
          permissionAxes: saved,
        });
        setAxesOverride(saved);
        // 与 PermissionAxesBadge 一致：同步审计后重拉，主流立即出现 A→B 行。
        void usePermissionChangeStore
          .getState()
          .load(approval.conversationId)
          .catch(() => {});
      })
      .catch((err) => notifyError(err, "切换失败"))
      .finally(() => setTrustBusy(false));
  };

  const spinnerOr = (decision: ApprovalDecision, icon: React.ReactNode) =>
    busy && clicked === decision ? (
      <Loader2 size={13} className="animate-spin" />
    ) : (
      icon
    );

  const onceButton = (
    <Button
      variant={preferTurnGrant ? "neutral" : "primary"}
      icon={spinnerOr("approve", <Check size={13} />)}
      disabled={busy}
      onClick={() => onDecide("approve")}
    >
      允许一次
    </Button>
  );
  const turnGrantButton =
    showTurnGrantButtons && supportsTurnGrant(approval.toolName) ? (
      <Button
        variant={preferTurnGrant ? "primary" : "neutral"}
        icon={spinnerOr("approve_always", <CheckCheck size={13} />)}
        disabled={busy}
        onClick={() => onDecide("approve_always")}
      >
        本轮内都允许
      </Button>
    ) : null;

  return (
    <DecisionCard
      tone="primary"
      animate={!attached}
      className={cn(
        "mx-0",
        attached &&
          "mt-0 rounded-b-none rounded-t-xl border-b-0 shadow-none animate-none",
      )}
    >
      <div className="flex items-start gap-2">
        <DecisionCardIcon tone="primary">
          <ShieldAlert size={16} />
        </DecisionCardIcon>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1">
            <p className="min-w-0 flex-1 text-sm text-foreground">
              <span className="font-medium">Agent 请求执行</span>
              <span className="text-muted-foreground"> · </span>
              <span className="font-medium">
                {toolLabelZh(approval.toolName)}
              </span>
            </p>
            <ManualHelpLink to={MANUAL_HELP.autonomy} />
          </div>
          {headline && (
            <SimpleTooltip label={headline}>
              <p
                className={`mt-0.5 truncate text-xs text-muted-foreground ${
                  isCodeExecute &&
                  typeof approval.arguments.purpose === "string" &&
                  approval.arguments.purpose.trim()
                    ? ""
                    : "font-mono"
                }`}
              >
                {headline}
              </p>
            </SimpleTooltip>
          )}
          {isFileBatch && batchOps.length > 0 && (
            <ol className="mt-1 max-h-40 list-decimal space-y-0.5 overflow-auto pl-4 font-mono text-xs text-muted-foreground">
              {batchOps.map((item, idx) => (
                <li key={`${idx}-${batchOpLine(item)}`} className="break-all">
                  {batchOpLine(item)}
                </li>
              ))}
            </ol>
          )}
          {isCodeExecute && riskTags.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {riskTags.map((tag) => (
                <Badge key={tag} tone="muted" className="font-normal">
                  {tag}
                </Badge>
              ))}
            </div>
          )}
          {forceOneShot && (
            <p className="mt-1 text-xs text-muted-foreground">
              安全熔断升格审批（启发式兜底，并非完整拦截）
              {escalationHint ? `：${escalationHint}` : ""}
            </p>
          )}
          {sensitivePathReadAsk && (
            <p className="mt-1 whitespace-pre-line text-xs text-muted-foreground">
              敏感路径读升格审批
              {escalationHint ? `：${escalationHint}` : ""}
            </p>
          )}
          {showManagedHint && (
            <p className="mt-1 text-xs text-muted-foreground">
              同类审批较频繁。可切换为
              <button
                type="button"
                className="mx-0.5 text-primary underline-offset-2 hover:underline"
                disabled={trustBusy}
                onClick={switchManaged}
              >
                托管
              </button>
              （下一回合生效；熔断仍在）。
            </p>
          )}
          {argEntries.length > 0 && (
            <Button
              variant="ghost"
              onClick={() => setExpanded((v) => !v)}
              className="mt-1 h-auto gap-1 px-0 py-0 text-xs text-muted-foreground hover:text-foreground"
              icon={
                expanded ? (
                  <ChevronDown size={13} />
                ) : (
                  <ChevronRight size={13} />
                )
              }
            >
              {expanded ? "收起参数" : "查看参数"}
            </Button>
          )}
          {expanded && isCodeExecute && codeText != null && (
            <div className="mt-1 space-y-1">
              {codeTruncated && (
                <p className="text-xs text-muted-foreground">代码预览已截断</p>
              )}
              <ApprovalHighlightedCode
                code={codeText}
                language={codeExecuteLanguage(approval.arguments)}
              />
            </div>
          )}
          {expanded && isCodeExecute && Object.keys(otherArgs).length > 0 && (
            <pre className="mt-1 max-h-40 overflow-auto rounded-lg bg-card/70 p-2 font-mono text-xs text-foreground">
              {JSON.stringify(otherArgs, null, 2)}
            </pre>
          )}
          {expanded &&
            !isCodeExecute &&
            Object.keys(displayArgs).length > 0 && (
              <pre className="mt-1 max-h-40 overflow-auto rounded-lg bg-card/70 p-2 font-mono text-xs text-foreground">
                {JSON.stringify(displayArgs, null, 2)}
              </pre>
            )}
        </div>
      </div>

      {showScopeNotice && (
        <p
          className="mt-2.5 pl-6 text-xs text-muted-foreground"
          data-testid="turn-grant-scope-notice"
        >
          {TURN_GRANT_SCOPE_NOTICE}
          {isFileOp ? FILE_CLASS_SCOPE_NOTICE : ""}
        </p>
      )}
      <div
        className={cn(
          "flex flex-wrap items-center gap-1.5 pl-6",
          showScopeNotice ? "mt-1.5" : "mt-2.5",
        )}
      >
        {preferTurnGrant ? (
          <>
            {turnGrantButton}
            {onceButton}
          </>
        ) : (
          <>
            {onceButton}
            {turnGrantButton}
          </>
        )}
        {isFileOp && showTurnGrantButtons && (
          <Button
            variant="neutral"
            icon={spinnerOr("approve_always_files", <FileCheck size={13} />)}
            disabled={busy}
            onClick={() => onDecide("approve_always_files")}
          >
            本轮内允许所有文件改动
          </Button>
        )}
        <Button
          variant="danger"
          icon={spinnerOr("deny", <X size={13} />)}
          disabled={busy}
          onClick={() => onDecide("deny")}
        >
          拒绝
        </Button>
      </div>
    </DecisionCard>
  );
}

function ApprovalHighlightedCode({
  code,
  language,
}: {
  code: string;
  language: string;
}) {
  const markdown = useMemo(
    () => fencedCodeMarkdown(code, language),
    [code, language],
  );
  return (
    <ReactMarkdown
      rehypePlugins={HIGHLIGHT_PLUGINS}
      components={{
        pre: CodeBlock,
        p: ({ children }) => <>{children}</>,
      }}
    >
      {markdown}
    </ReactMarkdown>
  );
}
