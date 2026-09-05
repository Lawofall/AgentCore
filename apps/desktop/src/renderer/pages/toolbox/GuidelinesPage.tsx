import { CapabilityPage } from "@/components/tools/CapabilityPage";
import { PromptCatalog } from "@/components/tools/PromptCatalog";

/** 工具箱「能力」组 → AI 提示词：常驻模板是全员基座 + 本回合三选一的角色身份；
 * 当前这批「技能」其实是几个内置工具的进阶用法（薄技能），
 * 不常驻、按需 consult 注入，故并入本页而非与工具并列——它们本质是 Prompt 注入、不是独立
 * 能力（决策见 UX §十二 / 术语表 三层模型）。部署上架的「能力包」在本页纯展示（包内技能
 * 已从薄技能区去重，避免与包卡片重复）。 */
export function GuidelinesPage() {
  return (
    <CapabilityPage title="AI 提示词" fill>
      {(data) => <PromptCatalog data={data} />}
    </CapabilityPage>
  );
}
