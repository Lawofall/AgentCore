import { APP_PATHS } from "../paths";
import { MANUAL_SECTION_IDS } from "../sectionIds";
import type { ManualChapterContent } from "../types";

/**
 * 看懂协作（选读）——结构化内容源；真图 / 真 UI 经 embed 槽接入。
 *
 * 口径：真选读、瘦身；用用户叙事讲清「团队怎么开工、怎么分批、怎么收口」，
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
      title: "看团队跑一遍",
      icon: "PlayCircle",
      blocks: [
        {
          type: "callout",
          variant: "info",
          text: [
            "这一章是选读——好奇团队在后台怎么转时再来。",
            { text: "不看不影响使用", strong: true },
            "。",
          ],
        },
        {
          type: "lead",
          text: [
            "下面这张图和你复杂任务里看到的同源，正在",
            { text: "跑一遍", strong: true },
            "：谁在干活、产出怎么往下交、最后怎么收口。",
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
      blocks: [
        {
          type: "lead",
          text: "你点发送之后，团队怎么接单、怎么分工、怎么交到你手里？简单对话直接答；复杂任务才拉起一支团队。",
        },
        {
          type: "cards",
          cols: 3,
          items: [
            {
              title: "接单准备",
              desc: "CEO 接到目标，备好该用的能力与上下文。闲聊当场答；需要产出、变更或多人协作时才组团。",
              icon: "Target",
            },
            {
              title: "分工推进",
              desc: "能一起干的同一批开干；有先后的等上游交活再解锁。中途要你拍板或放行时会停下来问——其他人不受影响。",
              icon: "UsersRound",
              highlight: true,
            },
            {
              title: "收尾交付",
              desc: "活干完回到 CEO，用自己的声音把结果交给你。中途断线也会尽量保住已完成的部分。",
              icon: "ShieldCheck",
            },
          ],
        },
        {
          type: "steps",
          items: [
            {
              title: "你说出目标",
              desc: "提问落库；协作图左侧出现「你的任务」端点。",
            },
            {
              title: "CEO 判断要不要组团",
              desc: "闲聊或简单问答直接作答；需要产出、变更或多人时才拉团队。",
            },
            {
              title: "分工图成形、分批推进",
              desc: "本批队员先亮成排队态——开跑前就能看见整张图。没有先后的同一批开干；有先后的等上游齐了再解锁下一批。",
            },
            {
              title: "队员各自干活",
              desc: "答案边写边流到节点上。需要你拍板或放行敏感操作时，会单独停下来问你。",
            },
            {
              title: "CEO 收口，答案落进气泡",
              desc: "活干完后 CEO 写一段简短概览；图上的汇聚点＝最终答案，点它可跳到气泡。",
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
      blocks: [
        {
          type: "lead",
          text: "并行、串行、辩论、嵌套、带现场续派——下面都是与对话同源的协作图，滚到才加载。",
        },
        {
          type: "paragraph",
          text: "辩论时的记分牌与终审",
          emphasis: true,
        },
        {
          type: "paragraph",
          text: "辩题、轮次、阵营比分——和辩论室顶栏同一套；终审简报也是真组件。",
        },
        { type: "embed", key: "ManualDebateScoreboardPreview" },
        { type: "embed", key: "ManualDebateFinalePreview" },
        { type: "embed", key: "MechanismScenarios" },
      ],
    },
  ],
};
