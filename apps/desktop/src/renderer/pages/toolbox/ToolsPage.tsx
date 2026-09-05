import { CapabilityPage } from "@/components/tools/CapabilityPage";
import { McpToolsSection } from "@/components/tools/McpToolsSection";
import { ToolCard } from "@/components/tools/ToolCard";
import { CATEGORY_META, CATEGORY_ORDER } from "@/components/tools/catalogMeta";
import { CatalogIconShell } from "@/components/ui";
import { useLlmProviders } from "@/hooks/useLlmProviders";
import { useModels } from "@/hooks/useModels";
import { catalogCategoryColorVar } from "@/lib/catalogColors";
import {
  TOOLS_GATE_HINT,
  TOOL_CALLING_TOOL_NAMES,
  needsToolsGateHint,
} from "@/lib/llmToolsGate";
import { defaultChatSupportsTools } from "@/services/llmProviders";

/** 工具箱「能力」组 → 工具：Agent 可调用的动作工具，按类分组，每个工具可展开调用参数。 */
export function ToolsPage() {
  const { data: llmProviders } = useLlmProviders();
  const { data: modelCatalog } = useModels();
  const showToolsHint = needsToolsGateHint(
    defaultChatSupportsTools(llmProviders, modelCatalog?.current?.provider_id),
  );

  return (
    <CapabilityPage title="工具">
      {(data) => {
        const grouped = CATEGORY_ORDER.map((category) => ({
          category,
          items: data.tools.filter((t) => t.category === category),
        })).filter((g) => g.items.length > 0);

        return (
          <div className="space-y-6">
            {grouped.map(({ category, items }) => {
              const meta = CATEGORY_META[category];
              const colorVar = catalogCategoryColorVar(category);
              const CatIcon = meta.icon;
              return (
                <div key={category}>
                  <h2 className="mb-2 flex items-center gap-1.5 text-muted-foreground text-xs">
                    <CatalogIconShell
                      colorVar={colorVar}
                      className="size-6 rounded-lg"
                    >
                      <CatIcon size={12} />
                    </CatalogIconShell>
                    {meta.label} · {items.length}
                  </h2>
                  <div className="grid grid-cols-[repeat(auto-fill,minmax(min(240px,100%),280px))] gap-3">
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
                </div>
              );
            })}
            <McpToolsSection />
          </div>
        );
      }}
    </CapabilityPage>
  );
}
