import { APP_PATHS } from "../paths";
import { MANUAL_SECTION_IDS } from "../sectionIds";
import type { ManualChapterContent } from "../types";

/**
 * 认识 AgentCore —— 第一章内容源。
 *
 * 口径：实用说明、不营销；上手步骤与空态引导（DraftEmptyState）对齐——平台代付、开箱即用，
 * 第一步就是说目标，BYOK 只是可选升级，不占开头把人支去外部站点。
 */
export const introChapter: ManualChapterContent = {
  id: "intro",
  path: APP_PATHS.toolbox.manual.intro,
  label: "认识 AgentCore",
  sections: [
    {
      id: MANUAL_SECTION_IDS.intro.what,
      title: "这是什么",
      icon: "Compass",
      blocks: [
        {
          type: "lead",
          text: "AgentCore 是 Multi-Agent AI 工作台。对着你的是一扇窗：轻问它直接答，要拆开做就开几段，做完把结论交给你。",
        },
        {
          type: "callout",
          variant: "tip",
          text: [{ text: "协作，是更高级的智能", strong: true }],
        },
        {
          type: "callout",
          variant: "info",
          text: "官网 https://fashitianxia.xyz · 桌面安装包 https://fashitianxia.xyz/download · 网页版 https://app.fashitianxia.xyz",
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.intro.mindset,
      title: "你怎么用",
      icon: "Target",
      ai: "off",
      blocks: [
        {
          type: "bullets",
          items: [
            {
              title: "说目标，别说步骤",
              desc: "怎么拆、先后顺序，交给对着你的这扇窗。",
            },
            {
              title: "轻问秒答，要拆开才开段",
              desc: "闲聊或简单问答直接回；要并行、独立验证或成篇时才再开几段。",
            },
            {
              title: "全程透明，随时插手",
              desc: "协作图实时可见；觉得跑偏了，一条消息就能纠正。",
            },
          ],
        },
        {
          type: "paragraph",
          text: "图上只有一种节点：一段工作，从打开到封口。",
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.intro.quickstart,
      title: "5 分钟上手",
      icon: "Rocket",
      ai: "off",
      blocks: [
        {
          type: "steps",
          items: [
            {
              title: "说目标",
              desc: "新建对话，用大白话描述你想要什么——平台代付，打开就能聊，不用先接入模型。空态页的建议任务可一键填入。",
            },
            {
              title: "看它干活",
              desc: "轻问秒回。要拆开做时，点右上角的「图」，能看见每一段在做什么。",
            },
            {
              title: "收结果",
              desc: "做完的结论回到对着你的这扇窗。文件落在你的文件夹里——打开的是本机文件夹就在你电脑上，否则在「我的文件」里。",
            },
          ],
        },
        {
          type: "callout",
          variant: "tip",
          text: "想立刻看见多人协作，试试：「分三路并行调研：竞品定价、用户痛点、渠道策略，各自产出一页摘要后由你汇总成决策简报。」",
        },
        {
          type: "callout",
          variant: "info",
          text: [
            "可选升级（不做也不影响用）：想换成自己的模型，桌面到 ",
            {
              text: "设置 · 服务商",
              link: { kind: "go", to: APP_PATHS.more.providers },
            },
            " 接入服务商或自带 API Key（BYOK，自担费用）；手机 ☰ 打开侧栏进「设置」再点「服务商」。平台额度临时不可用时会有公告。",
          ],
        },
      ],
    },
  ],
};
