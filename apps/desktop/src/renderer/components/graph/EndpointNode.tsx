import type { RunStatus } from "@/stores/execution";
import { Handle, type NodeProps, Position } from "@xyflow/react";
import {
  CheckCircle2,
  Loader2,
  Sparkles,
  UserRound,
  XCircle,
} from "lucide-react";
import { graphNodeDimClass, useGraphNodeDimmed } from "./graphHover";
import {
  useCaptainEndpointLive,
  useGraphDocumentMode,
  useInputEndpointLive,
} from "./graphLive";
import { useTerminalFlash } from "./useTerminalFlash";

/** 发起节点预览：去掉「N 个 worker：」人数前缀。
 * 图头 n/m 分母含 CEO 汇总，预览再报 worker 人数会读成口径打架。 */
export function initiatorNodePreview(
  taskSummary: string | null | undefined,
): string {
  const raw = (taskSummary ?? "").trim();
  if (!raw) return "";
  const withRoles = raw.match(/^\d+\s*个\s*workers?\s*[：:]\s*(.*)$/i);
  if (withRoles) return (withRoles[1] ?? "").trim();
  if (/^\d+\s*个\s*workers?$/i.test(raw)) return "";
  return raw;
}

/** Which bookend this node is: the synthetic user-input source, or the CEO
 * captain root 汇聚点 (the turn's reply engine, drawn as the team's climax). */
export type EndpointVariant = "input" | "captain";

interface EndpointNodeData {
  variant: EndpointVariant;
  /** Captain Document shell identity (Live reads status/preview by this). */
  runId?: string;
  /** Derived captain (汇聚点) status (ignored for the input node). */
  status?: RunStatus;
  /** Task summary (input) — kept short, the node clamps to two lines. */
  label?: string;
  /** Captain only: tail of the CEO's final answer, clamped to two lines, so
   * the climax node previews the team's deliverable like a worker previews its
   * output. Empty until the captain starts writing the answer. */
  preview?: string;
  /** Edge anchor orientation, driven by the active graph layout. */
  handleDirection?: "vertical" | "horizontal";
  /** Position in the plan, used to stagger the entrance animation. */
  enterIndex?: number;
  /** Lit (full-screen only) when the in-place panel surfaces this endpoint's
   * message — the user's prompt (input) / the CEO's final answer (captain) — so
   * the bookend glows like a drilled worker node. Mirrors AgentNode's `focused`;
   * its single source is the full-screen endpoint view (see GraphView). */
  focused?: boolean;
  /** Captain only: keyboard/mouse activation — jumps to the final answer.
   * Absent on the input node, which stays a passive label. */
  onActivate?: () => void;
  /** Captain only: a11y verb for the activation. Defaults to 查看最终回答. */
  actionLabel?: string;
  /** Captain only: overrides the derived status subtitle (e.g. coordination wait). */
  statusCaption?: string;
  [key: string]: unknown;
}

/** 汇聚点 ring + icon by derived status (mirrors AgentNode's mapping). */
const SINK_STYLES: Record<string, { ring: string; icon: React.ReactNode }> = {
  pending: {
    ring: "ring-muted-foreground/30",
    icon: <Sparkles size={15} className="text-muted-foreground" />,
  },
  running: {
    ring: "ring-primary",
    icon: <Loader2 size={15} className="animate-spin text-primary" />,
  },
  completed: {
    ring: "ring-success",
    icon: <CheckCircle2 size={15} className="text-success" />,
  },
  failed: {
    ring: "ring-destructive",
    icon: <XCircle size={15} className="text-destructive" />,
  },
  cancelled: {
    ring: "ring-muted-foreground/30",
    icon: <XCircle size={15} className="text-muted-foreground" />,
  },
  skipped: {
    ring: "ring-muted-foreground/30",
    icon: <Sparkles size={15} className="text-muted-foreground" />,
  },
};

export function EndpointNode({ data, id }: NodeProps) {
  const shell = data as EndpointNodeData;
  const documentMode = useGraphDocumentMode();
  const isInput = shell.variant === "input";
  const inputLive = useInputEndpointLive(shell.label ?? "");
  const captainLive = useCaptainEndpointLive(shell.runId ?? id);

  const live = isInput ? inputLive : captainLive;
  const status = (documentMode ? live.status : shell.status) ?? "pending";
  const statusCaption = documentMode ? live.statusCaption : shell.statusCaption;
  const focused = documentMode ? live.focused : !!shell.focused;
  const onActivate = documentMode ? live.onActivate : shell.onActivate;
  const previewText = documentMode
    ? isInput
      ? live.label
      : live.preview
    : isInput
      ? shell.label
      : shell.preview;

  const style = SINK_STYLES[status] ?? SINK_STYLES.pending;
  const running = !isInput && status === "running";
  const horizontal = shell.handleDirection === "horizontal";
  const interactive = !!onActivate;
  const highlighted = focused;
  const preview = isInput ? initiatorNodePreview(previewText) : previewText;
  const flashing = useTerminalFlash(status) && !isInput;
  const flashColor =
    status === "failed" ? "var(--destructive)" : "var(--success)";
  const enterDelay = Math.min((shell.enterIndex ?? 0) * 35, 280);
  const dimmed = useGraphNodeDimmed();

  const interactiveProps: React.HTMLAttributes<HTMLDivElement> = interactive
    ? {
        role: "button",
        tabIndex: 0,
        "aria-label": isInput
          ? "你的任务，对话发起，查看完整提问"
          : `CEO 汇总，${statusCaption ?? sinkLabel(status)}，${shell.actionLabel ?? "查看最终回答"}`,
        onKeyDown: (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onActivate?.();
          }
        },
      }
    : {};

  return (
    <>
      <Handle
        type="target"
        position={horizontal ? Position.Left : Position.Top}
        className="!bg-border"
      />
      <div className={graphNodeDimClass(dimmed)}>
        <div
          className="animate-graph-node-enter"
          style={{ animationDelay: `${enterDelay}ms` }}
        >
          <div
            {...interactiveProps}
            style={{ "--graph-flash-color": flashColor } as React.CSSProperties}
            className={`w-[210px] rounded-xl border px-3 py-2.5 shadow-sm outline-none ${
              isInput
                ? "border-border bg-muted/40"
                : `bg-card ring-2 ${style.ring}`
            } ${running ? "animate-pulse" : ""} ${flashing ? "animate-graph-node-flash" : ""} ${interactive ? "cursor-pointer" : ""} ${
              highlighted
                ? "outline outline-2 outline-offset-2 outline-primary"
                : interactive
                  ? "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary/60"
                  : ""
            }`}
          >
            <div className="flex items-center gap-2.5">
              <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted">
                {isInput ? (
                  <UserRound size={16} className="text-muted-foreground" />
                ) : (
                  style.icon
                )}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">
                  {isInput ? "你的任务" : "CEO 汇总"}
                </p>
                <p
                  className={`mt-0.5 truncate text-xs ${
                    running ? "text-primary" : "text-muted-foreground"
                  }`}
                  data-testid={isInput ? undefined : "captain-sink-label"}
                >
                  {isInput ? "对话发起" : (statusCaption ?? sinkLabel(status))}
                </p>
              </div>
            </div>

            {preview && (
              <p
                className={`mt-2 line-clamp-2 text-xs leading-snug ${
                  isInput
                    ? "text-muted-foreground/70"
                    : running
                      ? "text-foreground/80"
                      : "text-muted-foreground/80"
                }`}
                data-testid={isInput ? undefined : "captain-sink-preview"}
              >
                {preview}
              </p>
            )}
          </div>
        </div>
      </div>
      <Handle
        type="source"
        position={horizontal ? Position.Right : Position.Bottom}
        className="!bg-border"
      />
    </>
  );
}

function sinkLabel(status: RunStatus): string {
  const labels: Record<RunStatus, string> = {
    pending: "待汇总",
    running: "正在生成汇总…",
    completed: "已汇总",
    failed: "失败",
    cancelled: "已停止",
    skipped: "未执行",
  };
  return labels[status] ?? status;
}
