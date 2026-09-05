import { Badge } from "@/components/ui/badge";
import { agentColorVar, agentGlyph } from "@/lib/agentIdentity";
import { formatDuration } from "@/lib/format";
import type {
  ActAuthorizedBy,
  ActKind,
  ExecutionStatus,
} from "@/stores/execution";
import { Handle, type NodeProps, Position } from "@xyflow/react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Loader2,
  Pause,
  Sparkles,
  XCircle,
} from "lucide-react";
import { actAuthorizedByLabel } from "./actAuthLabels";
import { useGraphActions } from "./graphActions";
import { useActSummaryLive, useGraphDocumentMode } from "./graphLive";

/**
 * 幕摘要卡（批 R2 幕级 LOD）: one act of a multi-act turn folded into ONE node on the
 * collaboration graph's top-level chain.
 */
export interface ActSummaryData {
  actId: string;
  kind: ActKind | null;
  title: string | null;
  authorizedBy: ActAuthorizedBy | null;
  status?: ExecutionStatus;
  /** Distinct participant roles, first-seen order — drives the identity avatars. */
  roles: string[];
  agentCount?: number;
  completed?: number;
  total?: number;
  durationMs?: number | null;
  /** 图上指挥扫视: unanswered boss decisions folded on this act (待你拍板 chip). */
  pendingDecisions?: number;
  /** Edge anchor orientation, driven by the active graph layout. */
  handleDirection: "vertical" | "horizontal";
  /** 1-based act number for the「幕 N」eyebrow. */
  index: number;
  /** Click / keyboard activation — focuses this act (expands its DAG). */
  onActivate?: () => void;
  [key: string]: unknown;
}

const STATUS_STYLES: Record<
  ExecutionStatus,
  { ring: string; icon: React.ReactNode }
> = {
  planning: {
    ring: "ring-muted-foreground/30",
    icon: <Sparkles size={14} className="text-muted-foreground" />,
  },
  running: {
    ring: "ring-primary",
    icon: <Loader2 size={14} className="animate-spin text-primary" />,
  },
  paused: {
    ring: "ring-primary",
    icon: <Pause size={14} className="text-primary" />,
  },
  completed: {
    ring: "ring-success",
    icon: <CheckCircle2 size={14} className="text-success" />,
  },
  failed: {
    ring: "ring-destructive",
    icon: <XCircle size={14} className="text-destructive" />,
  },
  cancelled: {
    ring: "ring-muted-foreground/30",
    icon: <XCircle size={14} className="text-muted-foreground" />,
  },
};

const KIND_LABEL: Record<ActKind, string> = {
  multi_agent: "多智能体",
  debate: "辩论",
};

const AVATAR_CAP = 5;

export function ActSummaryNode({ data }: NodeProps) {
  const shell = data as ActSummaryData;
  const documentMode = useGraphDocumentMode();
  const live = useActSummaryLive(shell.actId);
  const actions = useGraphActions();

  const status =
    (documentMode ? live?.status : shell.status) ??
    ("planning" as ExecutionStatus);
  const roles = (documentMode ? live?.roles : shell.roles) ?? shell.roles;
  const agentCount =
    (documentMode ? live?.agentCount : shell.agentCount) ?? roles.length;
  const completed = (documentMode ? live?.completed : shell.completed) ?? 0;
  const total = (documentMode ? live?.total : shell.total) ?? 0;
  const durationMs = documentMode ? live?.durationMs : shell.durationMs;
  const pendingDecisions =
    (documentMode ? live?.pendingDecisions : shell.pendingDecisions) ?? 0;
  const onActivate = documentMode
    ? () => actions.focusAct(shell.actId)
    : shell.onActivate;

  const style = STATUS_STYLES[status] ?? STATUS_STYLES.planning;
  const horizontal = shell.handleDirection === "horizontal";
  const running = status === "running";
  const kindLabel = shell.kind ? KIND_LABEL[shell.kind] : null;
  const title = shell.title?.trim() || kindLabel || `幕 ${shell.index}`;
  const eyebrowParts = [`幕 ${shell.index}`];
  if (kindLabel) eyebrowParts.push(kindLabel);
  const auth = actAuthorizedByLabel(shell.authorizedBy);
  if (auth) eyebrowParts.push(auth);
  const shown = roles.slice(0, AVATAR_CAP);
  const overflow = roles.length - shown.length;
  const pct = total > 0 ? (completed / total) * 100 : 0;
  const interactive = !!onActivate;

  return (
    <>
      <Handle
        type="target"
        position={horizontal ? Position.Left : Position.Top}
        className="!bg-border"
      />
      <div
        {...(interactive
          ? {
              role: "button",
              tabIndex: 0,
              "aria-label": `${title}，${eyebrowParts.join("·")}，展开本幕`,
              onKeyDown: (e: React.KeyboardEvent) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onActivate?.();
                }
              },
            }
          : {})}
        className={`w-[280px] rounded-xl border bg-card px-3.5 py-3 shadow-sm outline-none ring-2 ${style.ring} ${
          running ? "animate-pulse" : ""
        } ${
          interactive
            ? "cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary/60"
            : ""
        }`}
      >
        <div className="flex items-center gap-2">
          <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted">
            {style.icon}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs text-muted-foreground">
              {eyebrowParts.join(" · ")}
            </p>
            <p className="min-w-0 truncate text-sm font-medium text-foreground">
              {title}
            </p>
          </div>
          {pendingDecisions > 0 && (
            <span className="flex shrink-0 items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
              <AlertTriangle size={11} />
              待你拍板{pendingDecisions > 1 ? ` ${pendingDecisions}` : ""}
            </span>
          )}
        </div>

        <div className="mt-2.5 flex items-center gap-1.5">
          {(() => {
            const seen = new Map<string, number>();
            return shown.map((role) => {
              const n = seen.get(role) ?? 0;
              seen.set(role, n + 1);
              return (
                <div
                  key={`${role}:${n}`}
                  title={role}
                  className="flex size-6 items-center justify-center rounded-full text-xs font-semibold"
                  style={{
                    backgroundColor: `color-mix(in oklab, ${agentColorVar(role)} 18%, transparent)`,
                    color: agentColorVar(role),
                  }}
                >
                  {agentGlyph(role)}
                </div>
              );
            });
          })()}
          {overflow > 0 && (
            <div className="flex size-6 items-center justify-center rounded-full bg-muted text-xs font-medium text-muted-foreground">
              +{overflow}
            </div>
          )}
          <span className="ml-auto shrink-0 text-xs text-muted-foreground">
            {agentCount} 个 Agent · {completed}/{total}
          </span>
        </div>

        {durationMs != null && durationMs > 0 && (
          <div className="mt-2 flex items-center">
            <Badge tone="muted" pill className="gap-1 font-medium">
              <Clock size={11} className="shrink-0" />
              用时 {formatDuration(durationMs)}
            </Badge>
          </div>
        )}

        {running && (
          <div className="mt-2.5 h-1 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-all duration-300"
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
      </div>
      <Handle
        type="source"
        position={horizontal ? Position.Right : Position.Bottom}
        className="!bg-border"
      />
    </>
  );
}
