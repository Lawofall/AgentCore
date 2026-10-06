import { CapabilityPage } from "@/components/tools/CapabilityPage";
import { PromptCatalog } from "@/components/tools/PromptCatalog";

/** 工具箱交代：一张货架（出厂卡在前，开关在标题行，必带打角标，有名字的夹点进去）。搜索由组装页统一收。 */
export function GuidelinesPage({
  query,
  suppressMiss = false,
  onMissChange,
}: {
  query?: string;
  suppressMiss?: boolean;
  onMissChange?: (miss: boolean) => void;
}) {
  return (
    <CapabilityPage>
      {(data) => (
        <PromptCatalog
          data={data}
          query={query}
          suppressMiss={suppressMiss}
          onMissChange={onMissChange}
        />
      )}
    </CapabilityPage>
  );
}
