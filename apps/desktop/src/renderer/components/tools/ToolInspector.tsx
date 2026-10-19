import { Badge, CatalogIconShell, SegmentedControl } from "@/components/ui";
import { catalogCategoryColorVar } from "@/lib/catalogColors";
import type { CapabilityTool } from "@/services/capabilities";
import { Wrench } from "lucide-react";
import { useState } from "react";
import { FACE_META, RESIDENT_LABEL } from "./catalogMeta";

type ToolParam = {
  name: string;
  description: string;
  required: boolean;
};

type DelegateGuide = "default" | "prefer";

const DELEGATE_GUIDE_ITEMS = [
  { value: "default" as const, label: "注意力尺" },
  { value: "prefer" as const, label: "优先派" },
];

export function toolGuideParams(tool: CapabilityTool): ToolParam[] {
  const schema = tool.parameters;
  if (!schema || typeof schema !== "object") return [];
  const root = schema as {
    properties?: Record<string, unknown>;
    required?: unknown;
  };
  const properties = root.properties;
  if (!properties || typeof properties !== "object") return [];
  const required = new Set(
    Array.isArray(root.required)
      ? root.required.filter((key): key is string => typeof key === "string")
      : [],
  );
  return Object.entries(properties).map(([name, spec]) => {
    const description =
      spec &&
      typeof spec === "object" &&
      "description" in spec &&
      typeof spec.description === "string"
        ? spec.description
        : "";
    return { name, description, required: required.has(name) };
  });
}

function preferGuide(tool: CapabilityTool): string {
  return tool.description_prefer?.trim() ?? "";
}

/** Inspector for an out-of-the-box tool. Lead = schema description; params; no schema dump. */
export function ToolInspector({
  tool,
  capabilityHint,
  hideChrome = false,
}: {
  tool: CapabilityTool;
  capabilityHint?: string;
  hideChrome?: boolean;
}) {
  const meta = FACE_META[tool.face];
  const Icon = meta?.icon ?? Wrench;
  const colorVar = catalogCategoryColorVar(tool.face);
  const params = toolGuideParams(tool);
  const prefer = preferGuide(tool);
  const showSwitch = Boolean(prefer);
  const [guide, setGuide] = useState<DelegateGuide>("default");
  const blurb = (
    (showSwitch && guide === "prefer" ? prefer : tool.description) ||
    tool.summary
  ).trim();

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {hideChrome ? null : (
        <header className="flex h-9 shrink-0 items-center gap-1.5 border-b border-border px-3">
          <CatalogIconShell colorVar={colorVar} className="size-6 rounded-lg">
            <Icon size={12} />
          </CatalogIconShell>
          <h2 className="min-w-0 truncate font-medium font-mono text-foreground text-sm">
            {tool.name}
          </h2>
          <Badge tone="muted" pill>
            {RESIDENT_LABEL.resident}
          </Badge>
          {meta ? (
            <Badge tone="muted" pill>
              {meta.label}
            </Badge>
          ) : null}
        </header>
      )}
      {capabilityHint ? (
        <p className="shrink-0 px-3 pt-3 text-xs text-muted-foreground/80">
          {capabilityHint}
        </p>
      ) : null}
      <div
        data-testid="tool-face-guide"
        className="min-h-0 flex-1 overflow-auto px-3 py-3"
      >
        {hideChrome ? (
          <p className="font-mono text-sm text-foreground">{tool.name}</p>
        ) : null}
        {showSwitch ? (
          <SegmentedControl
            aria-label="委派规则"
            value={guide}
            onChange={setGuide}
            items={DELEGATE_GUIDE_ITEMS}
            fit
            className={hideChrome ? "mt-3" : undefined}
          />
        ) : null}
        {blurb ? (
          <p
            data-testid="tool-face-guide-body"
            className={
              hideChrome || showSwitch
                ? "mt-3 text-sm leading-relaxed text-foreground"
                : "text-sm leading-relaxed text-foreground"
            }
          >
            {blurb}
          </p>
        ) : null}
        {params.length === 0 ? null : (
          <ul
            className={
              hideChrome || blurb
                ? "mt-4 flex flex-col gap-3"
                : "flex flex-col gap-3"
            }
          >
            {params.map((param) => (
              <li key={param.name}>
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-sm text-foreground">
                    {param.name}
                  </span>
                  {param.required ? (
                    <span className="text-xs text-muted-foreground">要填</span>
                  ) : null}
                </div>
                {param.description ? (
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {param.description}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
