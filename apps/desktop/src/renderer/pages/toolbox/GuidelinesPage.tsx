import { CapabilityPackCard } from "@/components/tools/CapabilityPackCard";
import { CapabilityPage } from "@/components/tools/CapabilityPage";
import { GuidelineBlock } from "@/components/tools/GuidelineBlock";
import { RoleIdentityBlock } from "@/components/tools/RoleIdentityBlock";
import { SkillCard } from "@/components/tools/SkillCard";
import {
  extractCeoIdentity,
  splitWorkerGuideline,
  workerContractFromGuidelines,
} from "@/lib/splitGuidelineRoles";

/** 工具箱「能力」组 → AI 提示词：常驻模板是全员基座 + 本回合三选一的角色身份 +
 * 队员交付合同；当前这批「技能」其实是几个内置工具的进阶用法（薄技能），
 * 不常驻、按需 consult 注入，故并入本页而非与工具并列——它们本质是 Prompt 注入、不是独立
 * 能力（决策见 UX §十二 / 术语表 三层模型）。部署上架的「能力包」在本页纯展示（包内技能
 * 已从薄技能区去重，避免与包卡片重复）。 */
export function GuidelinesPage() {
  return (
    <CapabilityPage note="常驻模板：全员共享准则 + 本回合三选一的角色身份 + 队员交付合同；工具进阶用法按需注入。每条回复的「收到的上下文」才是本回合实际提示词。">
      {(data) => {
        const packs = data.packs ?? [];
        const packSkillNames = new Set(
          packs.flatMap((pack) => pack.skills.map((s) => s.name)),
        );
        // Pack cards already list domain skills; keep the thin-skills strip for
        // platform mechanism skills only (delegate / debate / …).
        const thinSkills = data.skills.filter(
          (skill) => !packSkillNames.has(skill.name),
        );
        const ceoIdentity = extractCeoIdentity(data.guidelines.ceo_addon);
        const nestedIdentity = splitWorkerGuideline(
          data.guidelines.worker_captain,
        ).identity;
        const leafIdentity = splitWorkerGuideline(
          data.guidelines.worker_leaf,
        ).identity;
        const workerContract = workerContractFromGuidelines(
          data.guidelines.worker_leaf,
          data.guidelines.worker_captain,
        );
        return (
          <div className="space-y-8">
            {packs.length > 0 && (
              <section data-testid="capability-packs">
                <h2 className="font-medium text-foreground text-sm">能力包</h2>
                <p className="mt-1 mb-3 text-muted-foreground text-xs">
                  本部署已上架的垂直领域能力；包内技能已对全体用户生效，按需注入对话。
                </p>
                <div className="space-y-3">
                  {packs.map((pack) => (
                    <CapabilityPackCard key={pack.id} pack={pack} />
                  ))}
                </div>
              </section>
            )}

            <section className="space-y-3">
              <GuidelineBlock
                title="全员共享准则"
                subtitle="每个 Agent（主 Agent 与队员）共享的基座：表达风格、工具使用与安全。"
                text={data.guidelines.shared_base}
              />
              <RoleIdentityBlock
                ceoIdentity={ceoIdentity}
                nestedIdentity={nestedIdentity}
                leafIdentity={leafIdentity}
              />
              {workerContract ? (
                <GuidelineBlock
                  title="队员交付合同"
                  subtitle="叶子队员与可再委派的队员共用。主 Agent 不走这份合同；本回合原文在「收到的上下文」。"
                  text={workerContract}
                />
              ) : null}
            </section>

            {thinSkills.length > 0 && (
              <section>
                <h2 className="font-medium text-foreground text-sm">
                  工具进阶用法（薄技能）
                </h2>
                <p className="mt-1 mb-3 text-muted-foreground text-xs">
                  这些不是独立能力，而是几个内置工具（delegate / debate / revise
                  / ask_user）的进阶
                  用法——把「怎么用好它」从常驻工具描述里拆出来、按需注入：主
                  Agent 的「按需目录」平时只挂 一行触发说明，要用时才 consult
                  把完整指引拉回循环。等真正跨多工具的域级技能
                  （如合同审查）出现，再单独归类。
                </p>
                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                  {thinSkills.map((skill) => (
                    <SkillCard key={skill.name} skill={skill} />
                  ))}
                </div>
              </section>
            )}
          </div>
        );
      }}
    </CapabilityPage>
  );
}
