import { CapabilityPage } from "@/components/tools/CapabilityPage";
import { ToolCard } from "@/components/tools/ToolCard";
import { FACE_META, FACE_ORDER } from "@/components/tools/catalogMeta";
import { CATALOG_GRID_CLASS, CatalogIconShell } from "@/components/ui";
import { useLlmProviders } from "@/hooks/useLlmProviders";
import { useModels } from "@/hooks/useModels";
import { catalogCategoryColorVar } from "@/lib/catalogColors";
import {
  TOOLS_GATE_HINT,
  TOOL_CALLING_TOOL_NAMES,
  needsToolsGateHint,
} from "@/lib/llmToolsGate";
import { ConnectorsPage } from "@/pages/toolbox/ConnectorsPage";
import { defaultChatSupportsTools } from "@/services/llmProviders";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

function CatalogGroup({
  label,
  count,
  colorVar,
  icon: Icon,
  children,
}: {
  label: string;
  count: number;
  colorVar: string;
  icon: LucideIcon;
  children: ReactNode;
}) {
  return (
    <div>
      <h2 className="mb-2 flex items-center gap-1.5 text-muted-foreground text-xs">
        <CatalogIconShell colorVar={colorVar} className="size-6 rounded-lg">
          <Icon size={12} />
        </CatalogIconShell>
        {label} · {count}
      </h2>
      {children}
    </div>
  );
}

/** 工具箱 · 工具：一份图鉴。出厂动作按能力面；本机插头同款卡，点开配置。 */
export function ToolsPage() {
  const { data: llmProviders } = useLlmProviders();
  const { data: modelCatalog } = useModels();
  const showToolsHint = needsToolsGateHint(
    defaultChatSupportsTools(llmProviders, modelCatalog?.current?.provider_id),
  );

  return (
    <CapabilityPage fill>
      {(data) => {
        const grouped = FACE_ORDER.map((face) => ({
          face,
          items: data.tools.filter((t) => t.face === face),
        })).filter((g) => g.items.length > 0);

        return (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="space-y-6">
              {grouped.map(({ face, items }) => {
                const meta = FACE_META[face];
                return (
                  <CatalogGroup
                    key={face}
                    label={meta.label}
                    count={items.length}
                    colorVar={catalogCategoryColorVar(face)}
                    icon={meta.icon}
                  >
                    <div className={CATALOG_GRID_CLASS}>
                      {items.map((tool) => (
                        <ToolCard
                          key={tool.name}
                          tool={tool}
                          capabilityHint={
                            showToolsHint &&
                            TOOL_CALLING_TOOL_NAMES.has(tool.name)
                              ? TOOLS_GATE_HINT
                              : undefined
                          }
                        />
                      ))}
                    </div>
                  </CatalogGroup>
                );
              })}
              <ConnectorsPage />
            </div>
          </div>
        );
      }}
    </CapabilityPage>
  );
}
