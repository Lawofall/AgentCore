import { FileText } from "lucide-react";
import {
  type AgentNodeData,
  type AgentNodePresentation,
  PEEK_ARTIFACT_CAP,
  basename,
  statusLabel,
} from "./shared";

export function AgentNodePeek({
  d,
  p,
}: {
  d: AgentNodeData;
  p: AgentNodePresentation;
}) {
  const { color, glyph, showRoleTitle } = p.identity;

  return (
    <div className="space-y-2 py-1">
      <div className="flex items-center gap-2">
        <span
          className="flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
          style={{
            backgroundColor: `color-mix(in oklab, ${color} 18%, transparent)`,
            color,
          }}
        >
          {glyph}
        </span>
        {showRoleTitle ? (
          <span className="min-w-0 flex-1 truncate font-medium text-foreground">
            {d.role}
          </span>
        ) : (
          <span className="min-w-0 flex-1" />
        )}
        <span className="shrink-0 text-muted-foreground">
          {statusLabel(d.status)}
        </span>
      </div>
      {p.peekTags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {p.peekTags.map((t) => (
            <span
              key={t}
              className="rounded-full bg-muted px-1.5 py-0.5 text-muted-foreground"
            >
              {t}
            </span>
          ))}
        </div>
      )}
      {p.revisionFaceHint && (
        <div className="space-y-0.5">
          <p className="text-muted-foreground">改点</p>
          <p className="line-clamp-4 text-foreground">{p.revisionFaceHint}</p>
        </div>
      )}
      {d.task && (
        <div className="space-y-0.5">
          <p className="text-muted-foreground">任务</p>
          <p className="line-clamp-4 text-foreground">{d.task}</p>
        </div>
      )}
      {p.peekActivity && (
        <div className="space-y-0.5">
          <p className="text-muted-foreground">{p.peekActivity.heading}</p>
          <p
            className={`line-clamp-5 whitespace-pre-wrap break-words text-foreground ${
              p.peekActivity.italic ? "italic" : ""
            }`}
          >
            {p.peekActivity.text}
          </p>
        </div>
      )}
      {p.artifacts.length > 0 && (
        <div className="space-y-1">
          <p className="text-muted-foreground">
            产物{p.artifacts.length > 1 ? ` · ${p.artifacts.length}` : ""}
          </p>
          <div className="flex flex-wrap gap-1">
            {p.artifacts.slice(0, PEEK_ARTIFACT_CAP).map((path) => (
              <span
                key={path}
                title={path}
                className="flex max-w-full items-center gap-1 rounded-lg bg-muted px-1.5 py-0.5 text-foreground"
              >
                <FileText size={11} className="shrink-0" />
                <span className="truncate">{basename(path)}</span>
              </span>
            ))}
            {p.artifacts.length > PEEK_ARTIFACT_CAP && (
              <span className="rounded-lg bg-muted px-1.5 py-0.5 text-muted-foreground">
                +{p.artifacts.length - PEEK_ARTIFACT_CAP}
              </span>
            )}
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 border-t border-border pt-1.5 text-muted-foreground">
        <span>模型 {p.modelText}</span>
        <span aria-hidden>·</span>
        <span className="tabular-nums">{p.tokenText} tokens</span>
        {p.durationText && (
          <>
            <span aria-hidden>·</span>
            <span className="tabular-nums">{p.durationText}</span>
          </>
        )}
        {d.toolCount > 0 && (
          <>
            <span aria-hidden>·</span>
            <span className="tabular-nums">工具 {d.toolCount}</span>
          </>
        )}
        {d.costText && (
          <>
            <span aria-hidden>·</span>
            <span>{d.costText}</span>
          </>
        )}
      </div>
    </div>
  );
}
