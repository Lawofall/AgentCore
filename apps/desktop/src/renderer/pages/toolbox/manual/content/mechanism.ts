import { APP_PATHS } from "../paths";
import { MANUAL_SECTION_IDS } from "../sectionIds";
import type { ManualChapterContent } from "../types";

/**
 * 看懂协作（选读）——结构化内容源；真图 / 真 UI 经 embed 槽接入。
 *
 * 口径：真选读、瘦身；用用户叙事讲清「一段怎么开工、怎么接上下一段、怎么封口」，
 * 禁止实现术语（SSE / WaveScheduler / ReAct / finish_reason / depends_on /
 * Prepare·Execute·Finalize / max_parallel 等）。
 */
export const mechanismChapter: ManualChapterContent = {
  id: "mechanism",
  path: APP_PATHS.toolbox.manual.mechanism,
  label: "看懂协作（选读）",
  sections: [
    {
      id: MANUAL_SECTION_IDS.mechanism.live,
      title: "看图跑一遍",
      icon: "PlayCircle",
      ai: "off",
      blocks: [
        {
          type: "callout",
          variant: "info",
          text: [
            "这一章是选读——好奇这张图在后台怎么转时再来。",
            { text: "不看不影响使用", strong: true },
            "。",
          ],
        },
        {
          type: "lead",
          text: [
            "下面这张图和你复杂任务里看到的同源，正在",
            { text: "跑一遍", strong: true },
            "：哪段在做、结论怎么交给下一段、最后怎么封口。",
          ],
        },
        { type: "embed", key: "HeroGraph" },
        {
          type: "callout",
          variant: "tip",
          text: "亮蓝＝执行中，入边走粒子；变绿＝完成。背后半透明泳道是分轮推进的节奏。",
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.mechanism.legend,
      title: "看懂协作图",
      icon: "BookOpen",
      ai: "off",
      blocks: [
        {
          type: "lead",
          text: "节点、连线、颜色、徽章分别是什么意思——下面标清楚。",
        },
        { type: "embed", key: "GraphLegend" },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.mechanism.panorama,
      title: "从发消息到收答案",
      icon: "Layers",
      ai: "off",
      blocks: [
        {
          type: "lead",
          text: "你点发送之后，对着你的这扇窗怎么接、要不要再开段、结论怎么回到你手里？轻问直接答；要拆开才再开几段。",
        },
        {
          type: "cards",
          cols: 3,
          items: [
            {
              title: "接单准备",
              desc: "对着你的这扇窗接到目标，备好该用的能力与上下文。闲聊当场答；要并行、独立验证或成篇时才再开段。",
              icon: "Target",
            },
            {
              title: "分工推进",
              desc: "没有先后的同一批开；有先后的等上游封口再开。中途要你拍板或放行时，问题回到对着你的这扇窗，别的段继续。",
              icon: "UsersRound",
              highlight: true,
            },
            {
              title: "收尾交付",
              desc: "做完的结论回到对着你的这扇窗，由这扇窗告诉你。中途断线也会尽量保住已经封口的段。",
              icon: "ShieldCheck",
            },
          ],
        },
        {
          type: "steps",
          items: [
            {
              title: "你说出目标",
              desc: "提问落库。右上角可以切到图，开场这段就是第一段。",
            },
            {
              title: "要不要再开段",
              desc: "闲聊或简单问答直接作答；要并行、独立验证或成篇时才再开几段。",
            },
            {
              title: "分工图成形、分批推进",
              desc: "新开的段先是等待——开跑前就能看见整张图。没有先后的同一批开；有先后的等上游封口再开下一段。",
            },
            {
              title: "每段自己做",
              desc: "这段在写的话会流到节点上。需要你拍板或放行敏感操作时，问题回到对着你的这扇窗。",
            },
            {
              title: "最后一段封口",
              desc: "结论回到对着你的这扇窗。没有单独的汇总节点。",
            },
          ],
        },
        {
          type: "paragraph",
          text: "中途你会看见的真界面",
          emphasis: true,
        },
        {
          type: "paragraph",
          text: "需求没说清、或关键岔路，会弹出拍板卡；写文件、跑代码等敏感操作要你点「允许」才放行——下面就是对话里同一套组件。",
        },
        { type: "embed", key: "ManualCheckpointCardPreview" },
        { type: "embed", key: "ManualApprovalCardPreview" },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.mechanism.scenarios,
      title: "机制场景",
      icon: "LayoutGrid",
      ai: "off",
      blocks: [
        {
          type: "lead",
          text: "同时开、按顺序、辩论、再开一层、接着做——下面都是和对话同一张图，滚到才加载。",
        },
        {
          type: "paragraph",
          text: "辩论时的顶栏与终审",
          emphasis: true,
        },
        {
          type: "paragraph",
          text: "辩题、轮次、双方身份——和辩论室顶栏同一套；终审简报也是真组件。",
        },
        { type: "embed", key: "ManualDebateScoreboardPreview" },
        { type: "embed", key: "ManualDebateFinalePreview" },
        { type: "embed", key: "MechanismScenarios" },
      ],
    },
  ],
};
