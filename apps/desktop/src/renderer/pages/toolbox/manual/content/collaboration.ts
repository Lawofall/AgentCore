import { APP_PATHS } from "../paths";
import { MANUAL_SECTION_IDS, manualHref } from "../sectionIds";
import type { ManualChapterContent } from "../types";

/**
 * 看这张图 —— 结构化内容源（无 JSX）。
 *
 * 口径：按用户动作排；去重、去内部名；真组件演示归机制章。
 */
export const collaborationChapter: ManualChapterContent = {
  id: "collaboration",
  path: APP_PATHS.toolbox.manual.collaboration,
  label: "看这张图",
  sections: [
    {
      id: MANUAL_SECTION_IDS.collaboration.briefing,
      title: "怎么下任务",
      icon: "Target",
      ai: "off",
      blocks: [
        {
          type: "lead",
          text: "把目标说清楚。能直接答的，对着你的这扇窗自己答。要拆开做，它再开几段，你不用点名谁来做。",
        },
        {
          type: "paragraph",
          text: "一个好任务的三件套",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "目标",
              desc: "你要的是什么结果，一句话说清。",
            },
            {
              title: "约束",
              desc: "边界、口味、不要什么——比如「保持接口不变」「用中文」。",
            },
            {
              title: "期望产出",
              desc: "一段摘要？一个能跑的脚本？几个方案对比？说出形态。",
            },
          ],
        },
        {
          type: "doDont",
          good: {
            items: [
              "调研近 7 日成本趋势、定位异常点，产出一段 200 字摘要 + 一张趋势表。",
              "用 TypeScript 重写这个模块，保持现有接口不变，并补单元测试。",
            ],
          },
          bad: {
            items: ["看看成本。", "优化一下代码。"],
          },
        },
        {
          type: "paragraph",
          text: "想指定协作姿势时",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "并行",
              desc: "「分三路同时查：竞品定价、用户痛点、渠道策略，各自留下一段结论。」",
            },
            {
              title: "串行",
              desc: "「先调研再分析再写方案，上游产出喂给下游。」",
            },
            {
              title: "辩论",
              desc: "「就这个方案开一场正反辩论，再给我决策简报。」",
            },
          ],
        },
        {
          type: "callout",
          variant: "tip",
          text: "不确定怎么拆？只说目标就行——说清「要什么」永远比说清「怎么做」更重要。",
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.collaboration.progress,
      title: "看进度",
      icon: "Activity",
      blocks: [
        {
          type: "lead",
          text: "一场对话一张图。右上角「图」在正文和整列画布之间切换。图上只有一种节点：一段工作，从打开到封口。",
        },
        {
          type: "paragraph",
          text: "聊天里看，放大了细看",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "聊天视图",
              desc: "正文是对着你的这扇窗在说的话。图不嵌在每一条回复里。",
            },
            {
              title: "在画布打开",
              desc: "把这一回合放大成全屏：完整协作图、辩论过程都在这儿看。看完返回，聊天不受影响。",
            },
            {
              title: "拍板就在聊天里",
              desc: "检查点、审批、续跑、救火都就地出现在时间线上，不用切到别处。",
            },
          ],
        },
        {
          type: "callout",
          variant: "tip",
          text: [
            "图上符号与状态色见 ",
            {
              text: "看懂协作图",
              link: {
                kind: "go",
                to: manualHref(
                  "mechanism",
                  MANUAL_SECTION_IDS.mechanism.legend,
                ),
              },
            },
            "，此处不复述。",
          ],
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.collaboration.checkpoint,
      title: "检查点与审批",
      icon: "ShieldCheck",
      blocks: [
        {
          type: "lead",
          text: "只有猜错会把活做错时，团队才会停下来问你。能假设的小事会直接做，并在回复里写明假设。",
        },
        {
          type: "paragraph",
          text: "什么时候会停下",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "开场澄清",
              desc: "猜错会把活做错时，先短问再开工。语气、次要样式会按一个假设继续，并写明。",
            },
            {
              title: "关键岔路",
              desc: "影响全局的 A / B 选择，或不可逆操作，停下等你拍板。",
            },
            {
              title: "工具授权",
              desc: "敏感操作先征得你同意再执行——弹窗频率由权限配方决定。",
            },
          ],
        },
        {
          type: "paragraph",
          text: "拍板卡怎么点",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "拍板卡",
              desc: "两键：提交（带上选择与说明继续）+ 取消（结束本回合）。多题时右上编号切换，提交仍一次带走全部选择。",
            },
          ],
        },
        {
          type: "callout",
          variant: "info",
          text: [
            "写文件、跑代码等工具审批与 ",
            {
              text: "对话边界",
              link: {
                kind: "jump",
                to: MANUAL_SECTION_IDS.collaboration.autonomy,
              },
            },
            " 联动：边界决定能不能改文件、跑命令；拍板卡不受边界改写。",
          ],
        },
        {
          type: "callout",
          variant: "tip",
          text: "某一段答不了，问题回到对着你的这扇窗。别的段继续。关掉窗口也没关系：停在检查点的那段会留下，下次接着问。",
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.collaboration.autonomy,
      title: "对话边界",
      icon: "SlidersHorizontal",
      blocks: [
        {
          type: "lead",
          text: "对话边界决定这一场能改文件、跑命令、动这台电脑到哪一步。边界内不再逐次问。拍板卡仍会按需出现。",
        },
        {
          type: "paragraph",
          text: "三档怎么选",
          emphasis: true,
        },
        {
          type: "cards",
          cols: 2,
          items: [
            {
              title: "只看",
              desc: "只能看和搜索。不能改文件、跑命令、用浏览器。",
            },
            {
              title: "这个文件夹（推荐）",
              desc: "这个文件夹里可以改文件、跑命令、装依赖、用浏览器。装软件、推远程、毁灭形删除、读私钥仍会单独拦住。",
            },
            {
              title: "这台电脑",
              desc: "仅桌面。在这个文件夹之外，还能做本机操作。装软件、推远程、毁灭形删除、读私钥仍会单独拦住。",
            },
          ],
        },
        {
          type: "callout",
          variant: "tip",
          text: "对话输入区权限徽章里选，点「设为新会话默认」只影响之后新建的对话。已有会话在徽章里改，下一回合生效。升到这台电脑时会再确认一次。云端和网页没有「这台电脑」。",
        },
        {
          type: "callout",
          variant: "tip",
          text: [
            "与 ",
            {
              text: "检查点与审批",
              link: {
                kind: "jump",
                to: MANUAL_SECTION_IDS.collaboration.checkpoint,
              },
            },
            " 的关系：边界决定能不能做；拍板仍走检查点。不可逆动作不因提高边界而放行。",
          ],
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.collaboration.debate,
      title: "辩论室",
      icon: "Swords",
      blocks: [
        {
          type: "lead",
          text: "你要正反交锋时直接说——点了名就开跑，过程在辩论室里看。",
        },
        {
          type: "paragraph",
          text: "界面速览",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "顶栏",
              desc: "辩题、双方身份与轮次。",
            },
            {
              title: "剧本主列",
              desc: "逐轮发言、主持人小结与质询。",
            },
            {
              title: "终审舞台",
              desc: "裁决倾向与交接清单。",
            },
          ],
        },
        {
          type: "callout",
          variant: "info",
          text: [
            "入口：协作图状态条出现「辩论」时点「打开辩论室」；或在全屏回合详情切「辩论室」tab。图上符号见 ",
            {
              text: "看懂协作图",
              link: {
                kind: "go",
                to: manualHref(
                  "mechanism",
                  MANUAL_SECTION_IDS.mechanism.legend,
                ),
              },
            },
            "。收场后还想再辩？直接说——会重开一场，而不是复活上一场。",
          ],
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.collaboration.control,
      title: "中途插手",
      icon: "Hand",
      blocks: [
        {
          type: "lead",
          text: "跑偏了不用干等。停止在输入框，停的是对着你的这一轮。",
        },
        {
          type: "bullets",
          items: [
            {
              title: "停止",
              desc: "太慢或方向不对，点停止结束当前回合。",
            },
            {
              title: "停某一段",
              desc: "直接说停哪一段。这和停掉本机正在跑的程序不是一回事。",
            },
            {
              title: "图还在跑时说话",
              desc: "直接发送，马上进对着你的这扇窗。要等这段收工后再说，点「排队」。改某一段的方向，走详情「改这一段」。",
            },
            {
              title: "纠偏换方向",
              desc: "对某一段点「改这一段」：取消当前执行、带已有进度换方向。辩论另开，不在这张图里改。",
            },
            {
              title: "接着做",
              desc: "产物大致对、只改局部：再开一段，接在已经看见的结论上。不是从零重来。",
            },
            {
              title: "辩论进行中说话",
              desc: "辩论还在跑时，主输入框就是对这场说话（下一轮生效）。交锋够了会自己出结论；停止等于取消本回合，不会出简报。",
            },
            {
              title: "续聊或再发",
              desc: "有段失败时，失败留在图上。再发一条，用 graph 接在已经看见的结论上补。",
            },
            {
              title: "重新生成",
              desc: "方向全错或要整轮重来，从最后一条用户消息整轮再跑。",
            },
          ],
        },
        {
          type: "callout",
          variant: "warning",
          text: "发消息默认是在现有结论上接着做。想彻底换方向，明确说「推翻重来」，或用重新生成。",
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.collaboration.memory,
      title: "规矩与旧对话",
      icon: "Brain",
      blocks: [
        {
          type: "lead",
          text: "三层：当前这场对话；你写过的规矩（含你说「记住」记下的）；过往事情可查旧对话。系统不会自己总结一份关于你的简介。",
        },
        {
          type: "paragraph",
          text: "怎么留下规矩",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "直接说",
              desc: "「以后回答都用中文」「代码用 TypeScript」「别改公开 API」——说一次就够。",
            },
            {
              title: "查旧对话",
              desc: "「上次那个方案」可以搜以前的对话，或 @ 那场。工作区里的文件仍当场打开。",
            },
          ],
        },
        {
          type: "paragraph",
          text: "怎么改、怎么清",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "口头改写",
              desc: "直接说「忘掉上次说的……」或「改成……」即可。",
            },
            {
              title: "工具箱 · 提示词",
              desc: "打开工具箱的提示词页，可查看或调整所有对话共用的提示词。",
            },
          ],
        },
        {
          type: "paragraph",
          text: "可复用的编制",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "常驻",
              desc: "每回合都带着。适合全对话通用的规矩、口吻、禁区。",
            },
            {
              title: "按需",
              desc: "平时只挂一行，用到才翻。适合某类任务的拆法、检查单、写作模板。",
            },
            {
              title: "碰到文件",
              desc: "写清路径和那一句约束。读到或写到匹配的文件时带上全文。",
            },
            {
              title: "@ 点名",
              desc: "输入框 @ 某条按需提示词，这一句当场带上，不必改成常驻。",
            },
          ],
        },
        {
          type: "callout",
          variant: "info",
          text: [
            "入口：",
            {
              text: "工具箱",
              link: { kind: "go", to: APP_PATHS.toolbox.guidelines },
            },
            " · 提示词（仅桌面；手机 / 窄屏无工具箱，口头改规矩即可）。规矩来自你说的「记住」和提示词页；过往事情查旧对话。与数据留存、导出等关系见 ",
            {
              text: "数据与隐私",
              link: {
                kind: "go",
                to: manualHref(
                  "reference",
                  MANUAL_SECTION_IDS.reference.privacy,
                ),
              },
            },
            "。怎么写才会被翻开见 ",
            {
              text: "怎么写提示词",
              link: {
                kind: "jump",
                to: MANUAL_SECTION_IDS.collaboration.prompts,
              },
            },
            "。",
          ],
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.collaboration.prompts,
      title: "怎么写提示词",
      icon: "BookOpen",
      blocks: [
        {
          type: "lead",
          text: "按需那一句是目录上的找书签。只写名词，往往翻不到；写清干什么、什么时候用，才会在对的时候打开。",
        },
        {
          type: "paragraph",
          text: "放哪一区",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "常驻",
              desc: "每回合都要用的短规矩、口吻、禁区。不必靠一句话去找。",
            },
            {
              title: "按需",
              desc: "某一类任务的做法。平时只挂一行，相关时才翻开正文。",
            },
            {
              title: "碰到文件",
              desc: "针对某类路径的约束。路径写成 **/*.tsx 这种；* 不跨目录。",
            },
            {
              title: "@ 点名",
              desc: "这一句就要用：输入框 @ 那一条，不必改成常驻。没有斜杠菜单。",
            },
          ],
        },
        {
          type: "paragraph",
          text: "一句话介绍",
          emphasis: true,
        },
        {
          type: "paragraph",
          text: "按需、我的、市场上来的：这一句同时说清干什么、什么时候该翻开。空着可以保存，但不会主动翻；要当场用就 @。上架到市场必须有这句。",
        },
        {
          type: "doDont",
          good: {
            items: [
              "审、改、把关合同时用；用户拿出合同、条款、相对方文本时翻开。",
              "写周报：用本公司口径；用户说「这周周报 / 周会材料」时翻开。",
            ],
          },
          bad: {
            items: ["合同审查", "写作", "帮助"],
          },
        },
        {
          type: "paragraph",
          text: "正文写什么",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "可复用的做法",
              desc: "先分清这回在干什么，再用现有组队能力。不要另造一套固定角色。",
            },
            {
              title: "自家模板可以冻",
              desc: "口吻、检查单、交付形态随你。产品不会把「写得更专业」做成出厂课。",
            },
          ],
        },
        {
          type: "callout",
          variant: "tip",
          text: [
            "入口仅桌面：",
            {
              text: "工具箱",
              link: { kind: "go", to: APP_PATHS.toolbox.guidelines },
            },
            " · 提示词。手机 / 窄屏无工具箱，口头说规矩即可。市场装来的是同一类提示词，装完也靠这一句被翻开。",
          ],
        },
      ],
    },
  ],
};
