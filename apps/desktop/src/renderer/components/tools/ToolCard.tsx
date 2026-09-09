import {
  Badge,
  CatalogIconShell,
  CatalogTile,
  SectionLabel,
} from "@/components/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { artifactColorVar, catalogCategoryColorVar } from "@/lib/catalogColors";
import type { CapabilityTool } from "@/services/capabilities";
import { Plug, Wrench } from "lucide-react";
import { type ReactNode, useState } from "react";
import {
  APPROVAL_LABEL,
  FACE_META,
  RESIDENT_LABEL,
  availabilityLabel,
} from "./catalogMeta";

interface ParamProp {
  type?: string;
  description?: string;
}

function hasParamProps(parameters: Record<string, unknown>): boolean {
  const props = parameters.properties as Record<string, unknown> | undefined;
  return Boolean(props && Object.keys(props).length > 0);
}

/** Top-level call parameters (name · type — description). Nested shapes are
 * summarized by their top-level description, not expanded. */
function ToolParams({ parameters }: { parameters: Record<string, unknown> }) {
  const props = parameters.properties as Record<string, ParamProp> | undefined;
  if (!props || Object.keys(props).length === 0) return null;
  const required = new Set(
    Array.isArray(parameters.required) ? (parameters.required as string[]) : [],
  );
  return (
    <dl className="space-y-2">
      {Object.entries(props).map(([name, prop]) => (
        <div key={name} className="text-xs">
          <dt className="flex items-center gap-1.5">
            <span className="font-mono text-foreground">{name}</span>
            {required.has(name) && (
              <span className="text-destructive" title="必填">
                *
              </span>
            )}
            {prop?.type && (
              <span className="text-muted-foreground/70">{prop.type}</span>
            )}
          </dt>
          {prop?.description && (
            <dd className="mt-0.5 text-muted-foreground">{prop.description}</dd>
          )}
        </div>
      ))}
    </dl>
  );
}

function ToolTags({ tool }: { tool: CapabilityTool }): ReactNode {
  return (
    <>
      <Badge tone="muted" pill>
        {tool.resident ? RESIDENT_LABEL.resident : RESIDENT_LABEL.deferred}
      </Badge>
      <Badge tone="muted" pill>
        {availabilityLabel(tool.available_to)}
      </Badge>
      <Badge tone="muted" pill>
        {APPROVAL_LABEL[tool.approval]}
      </Badge>
    </>
  );
}

/** One tool tile: catalog summary on the shelf; click opens full params. */
export function ToolCard({
  tool,
  capabilityHint,
  source,
}: {
  tool: CapabilityTool;
  /** Soft capability note (e.g. tools probe unconfirmed) — never blocks the card. */
  capabilityHint?: string;
  /** Connector name when this action came from a plug, not a builtin face. */
  source?: string;
}) {
  const [open, setOpen] = useState(false);
  const sourced = Boolean(source);
  const Icon = sourced ? Plug : (FACE_META[tool.face]?.icon ?? Wrench);
  const colorVar = sourced
    ? artifactColorVar("connectors")
    : catalogCategoryColorVar(tool.face);
  const summary = tool.summary || tool.description;
  const detail = tool.description || tool.summary;

  return (
    <>
      <CatalogTile
        icon={<Icon size={18} />}
        colorVar={colorVar}
        title={tool.name}
        subtitle={source}
        description={summary}
        tags={<ToolTags tool={tool} />}
        onClick={() => setOpen(true)}
      >
        {capabilityHint ? (
          <p className="text-xs text-muted-foreground/80">{capabilityHint}</p>
        ) : null}
      </CatalogTile>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="flex max-h-[min(80vh,36rem)] flex-col">
          <DialogHeader>
            <div className="flex items-center gap-3">
              <CatalogIconShell colorVar={colorVar}>
                <Icon size={18} />
              </CatalogIconShell>
              <div className="min-w-0">
                <DialogTitle>{tool.name}</DialogTitle>
                {source ? (
                  <p className="mt-0.5 truncate text-xs text-muted-foreground">
                    {source}
                  </p>
                ) : null}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <ToolTags tool={tool} />
            </div>
          </DialogHeader>
          <div className="min-h-0 space-y-3 overflow-y-auto px-5 pb-5">
            {capabilityHint ? (
              <p className="text-xs text-muted-foreground/80">
                {capabilityHint}
              </p>
            ) : null}
            {detail ? (
              <DialogDescription className="text-sm">
                {detail}
              </DialogDescription>
            ) : null}
            <div className="space-y-2">
              <SectionLabel>调用参数</SectionLabel>
              {hasParamProps(tool.parameters) ? (
                <ToolParams parameters={tool.parameters} />
              ) : (
                <p className="text-xs text-muted-foreground">
                  该工具无调用参数
                </p>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
