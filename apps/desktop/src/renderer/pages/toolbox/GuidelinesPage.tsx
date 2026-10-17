import { CapabilityPage } from "@/components/tools/CapabilityPage";
import { PromptCatalog } from "@/components/tools/PromptCatalog";

/** 工具箱交代：一张货架（出厂卡在前，开关在标题行，常驻打角标，有名字的夹点进去）。 */
export function GuidelinesPage() {
  return (
    <CapabilityPage>{(data) => <PromptCatalog data={data} />}</CapabilityPage>
  );
}
