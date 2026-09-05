import { Badge, Button, CatalogTile } from "@/components/ui";
import { artifactColorVar, catalogCategoryColorVar } from "@/lib/catalogColors";
import type { CapabilityTool } from "@/services/capabilities";
import { ChevronRight, Plug, Wrench } from "lucide-react";
import { useState } from "react";
import {
  APPROVAL_LABEL,
  CATEGORY_META,
  availabilityLabel,
} from "./catalogMeta";

interface ParamProp {
  type?: string;
  description?: string;
}

/** Top-level call parameters as a 用法教学 list (name · type — description). Nested
 * shapes (e.g. delegate's task tree) are summarized by their top-level description, not
 * expanded — enough to teach "how you'd call it" without dumping the whole schema. */
function ToolParams({ parameters }: { parameters: Record<string, unknown> }) {
  const props = parameters.properties as Record<string, ParamProp> | undefined;
  if (!props || Object.keys(props).length === 0) {
    return (
      <p className="px-1 text-xs text-muted-foreground/70">
        该工具无调用参数。
      </p>
    );
  }
  const required = new Set(
    Array.isArray(parameters.required) ? (parameters.required as string[]) : [],
  );
  return (
    <dl className="space-y-1.5">
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
            <dd className="mt-0.5 line-clamp-3 text-muted-foreground">
              {prop.description}
            </dd>
          )}
        </div>
      ))}
    </dl>
  );
}

/** One tool tile: name · reach · approval, description, click-to-expand parameters. */
export function ToolCard({
  tool,
  capabilityHint,
  accent,
}: {
  tool: CapabilityTool;
  /** Soft capability note (e.g. tools probe unconfirmed) — never blocks expand/interaction. */
  capabilityHint?: string;
  /** MCP tools share the card, not a builtin `ToolCategory`. */
  accent?: "mcp";
}) {
  const [open, setOpen] = useState(false);
  const isMcp = accent === "mcp";
  const Icon = isMcp ? Plug : (CATEGORY_META[tool.category]?.icon ?? Wrench);
  const colorVar = isMcp
    ? artifactColorVar("connectors")
    : catalogCategoryColorVar(tool.category);
  return (
    <CatalogTile
      icon={<Icon size={18} />}
      colorVar={colorVar}
      title={tool.name}
      description={tool.description}
      descriptionClamp={!open}
      onClick={() => setOpen((v) => !v)}
      badge={
        <div className="flex shrink-0 items-center gap-1.5">
          {isMcp ? (
            <Badge tone="muted" pill>
              MCP
            </Badge>
          ) : null}
          <Badge tone="muted" pill>
            {availabilityLabel(tool.available_to)}
          </Badge>
          <Badge tone="muted" pill>
            {APPROVAL_LABEL[tool.approval]}
          </Badge>
        </div>
      }
    >
      <Button
        variant="ghost"
        onClick={() => setOpen((v) => !v)}
        className="h-auto self-start gap-1 px-0 py-0 text-muted-foreground hover:text-foreground"
        icon={
          <ChevronRight
            size={12}
            className={`transition-transform ${open ? "rotate-90" : ""}`}
          />
        }
      >
        调用参数
      </Button>
      {open ? (
        <div className="mt-2 border-border/60 border-t pt-2">
          {capabilityHint ? (
            <p className="mb-2 text-xs text-muted-foreground/80">
              {capabilityHint}
            </p>
          ) : null}
          <ToolParams parameters={tool.parameters} />
        </div>
      ) : null}
    </CatalogTile>
  );
}
