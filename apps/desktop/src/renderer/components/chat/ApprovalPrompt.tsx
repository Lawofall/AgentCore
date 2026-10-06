import {
  approvalBodyNeedsClip,
  countApprovalLines,
  firstApprovalLine,
} from "@/components/chat/approvalPreview";
import {
  codeExecuteLanguage,
  deriveCodeExecuteRiskTags,
  fencedCodeMarkdown,
  isPreviewTruncated,
} from "@/components/chat/codeExecuteApproval";
import { toolLabelZh } from "@/components/chat/toolLabelsZh";
import {
  Badge,
  Button,
  DecisionCard,
  DecisionCardFooter,
  DecisionCardIcon,
} from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useChatPaneId } from "@/lib/chatPane";
import { notifyError } from "@/lib/toast";
import { cn } from "@/lib/utils";
import {
  decideApproval,
  isFileOpTool,
  supportsTurnGrant,
} from "@/services/approvals";
import {
  type ApprovalView,
  isToolGranted,
  usePendingApprovals,
} from "@/stores/interactions";
import type { ApprovalDecision } from "@/types/events";
import { Loader2, ShieldAlert } from "lucide-react";
import {
  type ComponentPropsWithoutRef,
  type ReactNode,
  useMemo,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";

const HIGHLIGHT_PLUGINS: ComponentPropsWithoutRef<
  typeof ReactMarkdown
>["rehypePlugins"] = [[rehypeHighlight, { ignoreMissing: true }]];

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
        : "";
    return name ? `set_audio ${truncateSnippet(name)}` : "set_audio";
  }
  if (action === "restart_service") {
    return "restart_service Audiosrv";
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
    if (
      Array.isArray(ops) &&
      ops.length === 1 &&
      ops[0] &&
      typeof ops[0] === "object"
    ) {
      const one = ops[0] as Record<string, unknown>;
      const source =
        typeof one.source === "string" && one.source.trim()
          ? one.source.trim()
          : "";
      const destination =
        typeof one.destination === "string" && one.destination.trim()
          ? one.destination.trim()
          : "";
      if (source && destination) return `${source} → ${destination}`;
      const path =
        typeof one.path === "string" && one.path.trim() ? one.path.trim() : "";
      if (path) return path;
    }
    if (Array.isArray(ops)) return `本次共 ${ops.length} 项`;
  }
  if (toolName === "file_move" || toolName === "file_copy") {
    const source =
      typeof args.source === "string" && args.source.trim()
        ? args.source.trim()
        : typeof args.path === "string" && args.path.trim()
          ? args.path.trim()
          : "";
    const destination =
      typeof args.destination === "string" && args.destination.trim()
        ? args.destination.trim()
        : "";
    if (source && destination) return `${source} → ${destination}`;
    return source || destination || null;
  }
  if (toolName === "run" || toolName === "code_execute") {
    const cmd = args.command;
    if (typeof cmd === "string" && cmd.trim()) return cmd.trim();
  }
  if (toolName === "terminal") {
    const cmd = args.command;
    if (typeof cmd === "string" && cmd.trim()) return cmd.trim();
  }
  if (toolName === "read" || toolName === "write" || toolName === "edit") {
    const fp = typeof args.file_path === "string" ? args.file_path.trim() : "";
    return fp || null;
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

/** Verb / highlighter keys already implied by the card title or headline. */
const FACE_STRUCTURAL_KEYS = new Set(["subcommand", "action", "language"]);

/** Keys rendered as headline / badge / dedicated preview — not leftover rows. */
function isFacePreviewKey(toolName: string, key: string): boolean {
  if (toolName === "file_delete" && key === "permanent") return true;
  if (
    (toolName === "file_move" || toolName === "file_copy") &&
    (key === "source" || key === "destination" || key === "path")
  ) {
    return true;
  }
  if (
    toolName === "file_batch" &&
    (key === "operations" || key === "source" || key === "destination")
  ) {
    return true;
  }
  if (
    (toolName === "read" || toolName === "write" || toolName === "edit") &&
    key === "file_path"
  ) {
    return true;
  }
  if (toolName === "write" && key === "content") {
    return true;
  }
  if (toolName === "edit" && (key === "old_string" || key === "new_string")) {
    return true;
  }
  return false;
}

const LEFTOVER_ARG_LABELS: Record<string, string> = {
  overwrite: "覆盖",
  encoding: "编码",
  recursive: "递归",
  force: "强制",
  create: "创建",
  message: "说明",
  body: "正文",
  title: "标题",
  destination: "目标",
  source: "源",
  content: "内容",
  command: "命令",
  url: "网址",
  ref: "引用",
};

function formatLeftoverValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  if (value === true) return "是";
  if (Array.isArray(value)) {
    const parts = value.filter(
      (item): item is string =>
        typeof item === "string" && item.trim().length > 0,
    );
    if (parts.length === value.length && parts.length > 0) {
      return parts.join(", ");
    }
  }
  return JSON.stringify(value, null, 2);
}

function isEmptyApprovalArg(value: unknown): boolean {
  if (value == null || value === false) return true;
  if (typeof value === "string" && !value.trim()) return true;
  if (Array.isArray(value) && value.length === 0) return true;
  return false;
}

/** True when this value is already the visible headline (path / command / git remote…). */
function scalarOnApprovalFace(value: unknown, headline: string): boolean {
  if (typeof value === "number") {
    return (
      headline === String(value) ||
      headline.endsWith(` ${value}px`) ||
      headline.endsWith(` ${value}`)
    );
  }
  if (Array.isArray(value)) {
    const joined = value
      .filter(
        (item): item is string =>
          typeof item === "string" && item.trim().length > 0,
      )
      .map((item) => item.trim())
      .join(", ");
    return joined.length > 0 && scalarOnApprovalFace(joined, headline);
  }
  if (typeof value !== "string") return false;
  const text = value.trim();
  if (!text) return false;
  if (text === headline) return true;
  const snippet = truncateSnippet(text);
  if (snippet === headline) return true;
  if (text.length < 2) return false;
  return headline.endsWith(text) || headline.endsWith(snippet);
}

/**
 * Arguments not already on the card (title / headline / batch list).
 * Empty → nothing extra to dump.
 */
function leftoverApprovalArgs(
  toolName: string,
  args: Record<string, unknown>,
  headline: string | null,
): Record<string, unknown> {
  const leftover: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(args)) {
    if (isApprovalGateMetaKey(key) || FACE_STRUCTURAL_KEYS.has(key)) continue;
    if (isFacePreviewKey(toolName, key)) continue;
    if (toolName === "file_batch" && key === "operations") continue;
    if (toolName === "code_execute" && key === "code") continue;
    if (
      toolName === "delete_folder" &&
      (key === "folder_name" || key === "folder_id")
    ) {
      continue;
    }
    if (isEmptyApprovalArg(value)) continue;
    if (headline && scalarOnApprovalFace(value, headline)) continue;
    leftover[key] = value;
  }
  return leftover;
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
  const conversationId = useChatPaneId();
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
  const [clicked, setClicked] = useState<ApprovalDecision | null>(null);

  const isCodeExecute = approval.toolName === "code_execute";
  const isFileBatch = approval.toolName === "file_batch";
  const busy = approval.resolving;
  const isFileOp = isFileOpTool(approval.toolName);
  const {
    forceOneShot,
    sensitivePathReadAsk,
    hint: escalationHint,
  } = approvalEscalationTrack(approval.arguments);
  /** True fuse: no turn-scope grants (approve_always / approve_always_files). */
  const showTurnGrantButtons = !forceOneShot;
  const headline = primaryArg(approval.toolName, approval.arguments);

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
  const writeBody =
    approval.toolName === "write" &&
    typeof approval.arguments.content === "string" &&
    approval.arguments.content
      ? approval.arguments.content
      : null;
  const writeLineCount =
    writeBody != null ? countApprovalLines(writeBody) : null;
  const replaceOld =
    approval.toolName === "edit" &&
    typeof approval.arguments.old_string === "string" &&
    approval.arguments.old_string
      ? approval.arguments.old_string
      : null;
  const replaceNew =
    approval.toolName === "edit" &&
    typeof approval.arguments.new_string === "string" &&
    approval.arguments.new_string
      ? approval.arguments.new_string
      : null;
  const permanentDelete =
    approval.toolName === "file_delete" &&
    approval.arguments.permanent === true;
  const leftoverArgs = leftoverApprovalArgs(
    approval.toolName,
    approval.arguments,
    headline,
  );
  const leftoverEntries = Object.entries(leftoverArgs);

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

  const spinnerFor = (decision: ApprovalDecision) =>
    busy && clicked === decision ? (
      <Loader2 size={13} className="animate-spin" />
    ) : undefined;

  const onceButton = (
    <Button
      variant="primary"
      className="touch-target"
      icon={spinnerFor("approve")}
      disabled={busy}
      onClick={() => onDecide("approve")}
    >
      允许一次
    </Button>
  );
  const turnGrantButton =
    showTurnGrantButtons && supportsTurnGrant(approval.toolName) ? (
      <Button
        variant="outline"
        className="touch-target"
        icon={spinnerFor("approve_always")}
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
        "mx-0 overflow-hidden p-0",
        attached &&
          "mt-0 rounded-b-none rounded-t-xl border-b-0 shadow-none animate-none",
      )}
    >
      <div className="px-3 py-3">
        <div className="flex items-start gap-2">
          <DecisionCardIcon tone="primary">
            <ShieldAlert size={16} />
          </DecisionCardIcon>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium text-primary">请求执行</p>
            <p className="mt-0.5 flex min-w-0 items-baseline text-sm font-semibold text-foreground">
              <span className="shrink-0">{toolLabelZh(approval.toolName)}</span>
              {headline ? (
                <>
                  <span className="shrink-0 font-normal text-muted-foreground">
                    {" · "}
                  </span>
                  <SimpleTooltip label={headline}>
                    <span className="min-w-0 flex-1 truncate font-mono font-normal">
                      {headline}
                    </span>
                  </SimpleTooltip>
                </>
              ) : null}
              {writeLineCount != null ? (
                <span className="ml-1.5 shrink-0 tabular-nums font-normal text-muted-foreground/70">
                  {writeLineCount} 行
                </span>
              ) : null}
            </p>
            {permanentDelete && (
              <div className="mt-1">
                <Badge tone="destructive" className="font-normal">
                  永久删除
                </Badge>
              </div>
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
            {isCodeExecute && codeText != null && (
              <ApprovalCollapsiblePreview
                text={codeText}
                serverTruncated={codeTruncated}
                truncatedLabel="代码预览已截断"
                renderBody={(bodyClass) => (
                  <ApprovalHighlightedCode
                    code={codeText}
                    language={codeExecuteLanguage(approval.arguments)}
                    className={bodyClass}
                  />
                )}
              />
            )}
            {writeBody != null && (
              <ApprovalCollapsiblePreview
                text={writeBody}
                serverTruncated={isPreviewTruncated(writeBody)}
              />
            )}
            {replaceOld != null && replaceNew != null && (
              <ApprovalReplacePreview
                oldText={replaceOld}
                newText={replaceNew}
              />
            )}
            {leftoverEntries.length > 0 && (
              <dl className="mt-1 space-y-0.5">
                {leftoverEntries.map(([key, value]) => (
                  <div key={key} className="flex min-w-0 gap-2 text-xs">
                    <dt className="shrink-0 text-muted-foreground">
                      {LEFTOVER_ARG_LABELS[key] ?? key}
                    </dt>
                    <dd className="min-w-0 font-mono text-foreground">
                      <LeftoverArgValue value={value} />
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        </div>
      </div>

      <DecisionCardFooter tone="primary" className="mt-0">
        {onceButton}
        {turnGrantButton}
        {isFileOp && showTurnGrantButtons && (
          <Button
            variant="outline"
            className="touch-target"
            icon={spinnerFor("approve_always_files")}
            disabled={busy}
            onClick={() => onDecide("approve_always_files")}
          >
            本轮内允许所有文件改动
          </Button>
        )}
        <Button
          variant="danger"
          className="touch-target"
          icon={spinnerFor("deny")}
          disabled={busy}
          onClick={() => onDecide("deny")}
        >
          拒绝
        </Button>
      </DecisionCardFooter>
    </DecisionCard>
  );
}

function previewBodyClass(long: boolean, open: boolean): string {
  return cn(
    "whitespace-pre-wrap break-all font-mono text-xs text-muted-foreground",
    long && !open && "line-clamp-3",
    long && open && "max-h-40 overflow-auto",
  );
}

function ApprovalPreviewToggle({
  open,
  onToggle,
}: {
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className="text-xs font-medium text-muted-foreground hover:text-foreground"
    >
      {open ? "收起" : "展开"}
    </button>
  );
}

function ApprovalCollapsiblePreview({
  text,
  serverTruncated = false,
  truncatedLabel = "预览已截断",
  compact = false,
  renderBody,
}: {
  text: string;
  serverTruncated?: boolean;
  truncatedLabel?: string;
  compact?: boolean;
  renderBody?: (className: string) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const long = approvalBodyNeedsClip(text);
  const bodyClass = previewBodyClass(long, open);
  return (
    <div className={cn(!compact && "mt-1", "space-y-1")}>
      {serverTruncated && open ? (
        <p className="text-xs text-muted-foreground">{truncatedLabel}</p>
      ) : null}
      {renderBody ? (
        renderBody(bodyClass)
      ) : (
        <pre className={bodyClass}>{text}</pre>
      )}
      {long ? (
        <ApprovalPreviewToggle
          open={open}
          onToggle={() => setOpen((v) => !v)}
        />
      ) : null}
    </div>
  );
}

function ApprovalReplacePreview({
  oldText,
  newText,
}: {
  oldText: string;
  newText: string;
}) {
  const [open, setOpen] = useState(false);
  const long = approvalBodyNeedsClip(oldText) || approvalBodyNeedsClip(newText);
  if (long && open) {
    return (
      <div className="mt-1 space-y-1">
        <p className="text-xs text-muted-foreground">原文</p>
        <pre className={previewBodyClass(true, true)}>{oldText}</pre>
        <p className="text-xs text-muted-foreground">替换为</p>
        <pre className={previewBodyClass(true, true)}>{newText}</pre>
        <ApprovalPreviewToggle open onToggle={() => setOpen(false)} />
      </div>
    );
  }
  return (
    <div className="mt-1 space-y-1">
      <div className="space-y-0.5 font-mono text-xs">
        <p className="truncate text-destructive">
          - {truncateSnippet(firstApprovalLine(oldText))}
        </p>
        <p className="truncate text-success">
          + {truncateSnippet(firstApprovalLine(newText))}
        </p>
      </div>
      {long ? (
        <ApprovalPreviewToggle open={false} onToggle={() => setOpen(true)} />
      ) : null}
    </div>
  );
}

function LeftoverArgValue({ value }: { value: unknown }) {
  const formatted = formatLeftoverValue(value);
  if (approvalBodyNeedsClip(formatted)) {
    return <ApprovalCollapsiblePreview text={formatted} compact />;
  }
  return <span className="whitespace-pre-wrap break-all">{formatted}</span>;
}

function ApprovalHighlightedCode({
  code,
  language,
  className,
}: {
  code: string;
  language: string;
  className?: string;
}) {
  const markdown = useMemo(
    () => fencedCodeMarkdown(code, language),
    [code, language],
  );
  return (
    <ReactMarkdown
      rehypePlugins={HIGHLIGHT_PLUGINS}
      components={{
        pre: ({ children }) => <pre className={className}>{children}</pre>,
        p: ({ children }) => <>{children}</>,
      }}
    >
      {markdown}
    </ReactMarkdown>
  );
}
