import { CapabilityPage } from "@/components/tools/CapabilityPage";
import { PromptCatalog } from "@/components/tools/PromptCatalog";

/** 工具箱「提示词」：常驻 / 按需两区只放能换档的提示词；出厂工具与连接器跟在后面一块「工具」。官方 HOW 收进按需「官方」区，只读。 */
export function GuidelinesPage() {
  return (
    <CapabilityPage>{(data) => <PromptCatalog data={data} />}</CapabilityPage>
  );
}
