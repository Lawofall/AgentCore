import { CapabilityPage } from "@/components/tools/CapabilityPage";
import { PromptCatalog } from "@/components/tools/PromptCatalog";

/** 工具箱「提示词」：常驻 / 按需两区。偏好·画像锁在常驻；官方 HOW 平铺在按需底部，只读。 */
export function GuidelinesPage() {
  return (
    <CapabilityPage fill>
      {(data) => <PromptCatalog data={data} />}
    </CapabilityPage>
  );
}
