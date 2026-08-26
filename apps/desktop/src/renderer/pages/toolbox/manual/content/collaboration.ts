import { APP_PATHS } from "../paths";
import { MANUAL_SECTION_IDS, manualHref } from "../sectionIds";
import type { ManualChapterContent } from "../types";

/**
 * 指挥你的团队 —— 结构化内容源（无 JSX）。
 *
 * 口径：按用户动作排；去重、去内部名；真组件演示归机制章。
 */
export const collaborationChapter: ManualChapterContent = {
  id: "collaboration",
  path: APP_PATHS.toolbox.manual.collaboration,
  label: "指挥你的团队",
  sections: [
    {
      id: MANUAL_SECTION_IDS.collaboration.briefing,
      title: "怎么下任务",
      icon: "Target",
      blocks: [
        {
          type: "lead",
          text: "把目标说清楚，团队产出才准。能直接答的 CEO 自己答；要动手做的才拉团队——角色由 CEO 临时分配，你不用点名。",
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
              desc: "「分三路并行调研：竞品定价、用户痛点、渠道策略，各自产出一页摘要后由你汇总。」",
            },
            {
              title: "串行",
              desc: "「先调研再分析再写方案，上游产出喂给下游。」",
            },
            {
              title: "辩论",
              desc: "「就这个方案开一场正反辩论，再给我决策简报。」或指定红队挑刺 / 多方圆桌。",
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
          text: "干到哪了、谁在忙、有没有卡住——聊天里随时能看见，想看大图再放大。",
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
              desc: "唯一的常驻视图：流式输出 + 内嵌协作图 + 状态条，编制与进度一眼可见。",
            },
            {
              title: "在画布打开",
              desc: "把这一回合放大成全屏：完整协作图、辩论过程、多次接续的对比都在这儿看。看完返回，聊天不受影响。",
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
          text: "关键决定或拿不准时，团队会停下来问你，不会自作主张。",
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
              desc: "需求能做但还不够明确时，CEO 先给起步计划 + 重点问题，让你补齐再开工。",
            },
            {
              title: "关键岔路",
              desc: "影响全局的 A / B 选择，或不可逆操作，停下等你拍板。",
            },
            {
              title: "工具授权",
              desc: "敏感操作先征得你同意再执行——弹窗频率由权限配方决定。",
            },
            {
              title: "计划复核",
              desc: "流水线波间闸门：上游做完、下游待跑时，可先确认再放行。",
            },
          ],
        },
        {
          type: "paragraph",
          text: "拍板卡怎么点（两类按键不同）",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "拍板卡",
              desc: "两键：提交（带上选择与说明继续）+ 取消（结束本回合）。多题时右上编号切换，提交仍一次带走全部选择。没有单独的「继续 / 调整」。",
            },
            {
              title: "计划复核",
              desc: "三键：继续 / 调整（备注注入未跑下游）/ 取消。",
            },
          ],
        },
        {
          type: "callout",
          variant: "info",
          text: [
            "写文件、跑代码等工具审批与 ",
            {
              text: "自主度",
              link: {
                kind: "jump",
                to: MANUAL_SECTION_IDS.collaboration.autonomy,
              },
            },
            " 联动：配方越托管，同类能力越少逐次弹窗。拍板卡与计划复核不受配方改写。",
          ],
        },
        {
          type: "callout",
          variant: "tip",
          text: "某个队员干到一半卡住，会单独「升级」上来问你——不会拖住其他还在并行跑的队员。关掉窗口也没关系：停在检查点的任务会被存住，下次从断点续。",
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.collaboration.autonomy,
      title: "自主度",
      icon: "SlidersHorizontal",
      blocks: [
        {
          type: "lead",
          text: "权限配方管「改文件 / 执行命令 / 组团卡」弹多少次。拍板卡与计划复核仍会按需出现。",
        },
        {
          type: "paragraph",
          text: "三个配方怎么选",
          emphasis: true,
        },
        {
          type: "cards",
          cols: 2,
          items: [
            {
              title: "谨慎",
              desc: "改文件逐次问（云端与本地都问）；不预授执行；组团卡按规则。最稳，批量改文件时会很吵。",
            },
            {
              title: "少打断（推荐）",
              desc: "本会话信任改文件；自动执行；组团卡按规则；本机会话信任。",
            },
            {
              title: "托管",
              desc: "本会话信任改文件；自动执行；跳过组团卡；本机会话信任。拍板检查点仍会出现。",
            },
          ],
        },
        {
          type: "callout",
          variant: "tip",
          text: "桌面：对话输入区权限徽章选三配方之一后，点「设为新会话默认」写入账户默认（只影响之后新建的对话；自定义权限轴不可设为默认）。手机仍可在设置改默认。已有会话请在徽章切配方或权限轴，下一回合生效。",
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
            " 的关系：配方减的是工具审批与组团卡疲劳；拍板与计划复核仍走检查点。非法组合「免审执行 + 改文件逐次问」选不出。",
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
          text: "需要互审、压力测试或铺开多方观点时，CEO 会开一场辩论——过程本身也是产物。",
        },
        {
          type: "paragraph",
          text: "三种形态",
          emphasis: true,
        },
        {
          type: "cards",
          cols: 3,
          items: [
            {
              title: "正反辩论",
              desc: "正 / 反对称攻防，适合二选一决策。",
            },
            {
              title: "红队挑刺",
              desc: "红队单向找风险，方案方回应，产出风险清单。",
            },
            {
              title: "多方圆桌",
              desc: "3+ 视角碰撞，铺开观点光谱。",
            },
          ],
        },
        {
          type: "paragraph",
          text: "你能做什么",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "站队",
              desc: "记分牌上点选倾向——仅你可见，绝不改写 AI 裁决。",
            },
            {
              title: "掌舵",
              desc: "轮间轻量引导（追问 / 加角度 / 够了收），下一轮生效，不硬停辩论。",
            },
          ],
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
              title: "记分牌",
              desc: "辩题、形态、轮次与阵营比分。",
            },
            {
              title: "剧本主列",
              desc: "逐轮发言、主持人小结、质询与掌舵入口。",
            },
            {
              title: "终审舞台",
              desc: "裁决倾向、战果对照、交接清单。",
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
            "。收场后还想再辩？直接对 CEO 说话——会重开一场，而不是复活上一场。",
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
          text: "跑偏了不用干等——随时能停、能纠偏、能续派。",
        },
        {
          type: "bullets",
          items: [
            {
              title: "停止",
              desc: "太慢或方向不对，点停止结束当前回合。",
            },
            {
              title: "纠偏换方向",
              desc: "对某个队员点「立即改此人」：取消当前执行、带已有进度换方向。辩论回合不可用——想改辩题请重开一场。",
            },
            {
              title: "带现场续派",
              desc: "产物大致对、只改局部：唤回原队员带完整现场接着改（口语也叫「同人接续」），协作图上挂「续 ×N」，可打开版本对比。不是从零重来。",
            },
            {
              title: "续聊或再发",
              desc: "部分队员失败时，失败会留在图上可见；对 CEO 续聊或再发一条，让团队接着补。",
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
          text: "发消息默认是「在现有基础上改」。想彻底换方向，明确说「推翻重来」，或用重新生成。续派适合局部打磨；整轮方向错了用重新生成。",
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.collaboration.memory,
      title: "记忆与偏好",
      icon: "Brain",
      blocks: [
        {
          type: "lead",
          text: "不用每次重新交代背景——偏好与工作习惯会跨对话延续。",
        },
        {
          type: "paragraph",
          text: "怎么让它记住",
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
              title: "越用越懂",
              desc: "常用口味与工作习惯会沉淀下来；换个对话也不用重新介绍自己和手头的事。",
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
              title: "文件页 · 全局设定",
              desc: "打开文件页顶部的「全局设定」，可查看、编辑或清理。",
            },
          ],
        },
        {
          type: "callout",
          variant: "info",
          text: [
            "入口：",
            {
              text: "文件",
              link: { kind: "go", to: APP_PATHS.files },
            },
            " → 全局设定。记忆来自你的对话偏好；与数据留存、导出等关系见 ",
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
            "。",
          ],
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.collaboration.workflow,
      title: "工作流",
      icon: "Workflow",
      blocks: [
        {
          type: "lead",
          text: "把「谁做什么、先后怎么排」画成一张可复用的图——下次同类的活直接照这张图跑，不用再从头交代一遍。",
        },
        {
          type: "paragraph",
          text: "主路径：去工具箱设计",
          emphasis: true,
        },
        {
          type: "steps",
          items: [
            {
              title: "打开工具箱 · 工作流",
              desc: [
                "到 ",
                {
                  text: "工具箱 · 工作流",
                  link: { kind: "go", to: APP_PATHS.toolbox.workflows.root },
                },
                "。需要固定拆法时在这里新建或套官方模板，再在画布上设计。",
              ],
            },
            {
              title: "新建空白图",
              desc: "点「新建工作流」从空白画布起步，自己排队员、关卡和先后。",
            },
            {
              title: "在画布上设计，再跑一次或交给定时",
              desc: "画好后点「跑一次」选个工作区就能直起；也可以让自动化里的任务绑着它按时跑。",
            },
          ],
        },
        {
          type: "paragraph",
          text: "画布上能摆什么",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "队员步骤",
              desc: "一个队员干一件事：写清角色、任务说明和要交什么。",
            },
            {
              title: "等人关卡",
              desc: "跑到这儿停下来等你看一眼，你放行后下游才继续。",
            },
            {
              title: "连线定先后",
              desc: "没有连线的步骤同一批并行；有连线的等上游交活再解锁。",
            },
            {
              title: "开跑按图执行",
              desc: "跑的时候结构锁定，不临场加人改序，也不再由 CEO 即兴组队；要改结构就回画布改一版。权限仍按你选的自主度，不因为有图就自动放宽。",
            },
          ],
        },
        {
          type: "paragraph",
          text: "也可以从官方模板起步",
          emphasis: true,
        },
        {
          type: "paragraph",
          text: "工作流页顶部列着官方模板（多角摸底、调研报告成文、方案对比选型等），先看目标再挑。点「使用」是复制一份成你自己的工作流，再改名字和步骤；原模板只读，改坏了随时重新复制一份。",
        },
        {
          type: "callout",
          variant: "info",
          text: [
            "工作流管「活儿怎么拆」，",
            {
              text: "自动化",
              link: {
                kind: "jump",
                to: MANUAL_SECTION_IDS.collaboration.automation,
              },
            },
            " 管「什么时候跑」。日常聊天不需要它——没绑工作流时，CEO 照常即兴组队。",
          ],
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.collaboration.automation,
      title: "自动化",
      icon: "CalendarClock",
      blocks: [
        {
          type: "lead",
          text: "常做的活配上定时或 Webhook，到点由 CEO 自动开一轮协作；你回来只在收件箱看摘要，处理待你拍板的那几条。",
        },
        {
          type: "paragraph",
          text: "一个任务要配什么",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "触发方式",
              desc: "定时（每天 / 每周 / 自定义 cron）或 Webhook（外部系统 POST 一下就开跑，事件正文会带进本轮上下文）。一个任务只选一种，密钥只在创建或轮换时显示一次。",
            },
            {
              title: "目标",
              desc: "到点要完成什么，写法与在对话里下任务一样——目标、约束、期望产出。",
            },
            {
              title: "工作区",
              desc: "产物落在哪个云工作区。任务只能绑云工作区：你关机的时候，本机文件夹跑不了。",
            },
            {
              title: "自主度",
              desc: [
                "和对话里同一套",
                {
                  text: "配方",
                  link: {
                    kind: "jump",
                    to: MANUAL_SECTION_IDS.collaboration.autonomy,
                  },
                },
                "。无人值守时若撞上要你拍板的检查点，这一轮会挂起等你，不会替你做决定。",
              ],
            },
            {
              title: "绑一张工作流（可选）",
              desc: "绑了就按图跑，目标文案只当本轮补充；不绑就按目标文案让 CEO 即兴组队。",
            },
          ],
        },
        {
          type: "paragraph",
          text: "结果去哪看",
          emphasis: true,
        },
        {
          type: "bullets",
          items: [
            {
              title: "收件箱",
              desc: "每次运行一条：成功摘要、失败原因、待你拍板的挂起项；tab 上的红点是还没处理的条数。",
            },
            {
              title: "点进对话",
              desc: "每次运行都是一轮真实对话——点「去拍板 / 进对话」照样看协作图、回检查点，和平时一样。",
            },
            {
              title: "收尾",
              desc: "看完标为已读；失败的那条可以重新触发一次。",
            },
          ],
        },
        {
          type: "paragraph",
          text: "系统任务",
          emphasis: true,
        },
        {
          type: "paragraph",
          text: "自动化页顶部是平台预制的系统任务（如每日对话复盘）：目标由系统托管、不可改，你只配触发时间、复盘范围与报告落点。开启前它不会跑，之后也能随时暂停。",
        },
        {
          type: "callout",
          variant: "tip",
          text: "想确认配得对不对，不用等到点——在任务上点「立即触发」，当场跑一轮看看。",
        },
        {
          type: "callout",
          variant: "info",
          text: [
            "入口：",
            {
              text: "工具箱 · 自动化",
              link: { kind: "go", to: APP_PATHS.toolbox.automations.root },
            },
            "；结果在 ",
            {
              text: "收件箱",
              link: { kind: "go", to: APP_PATHS.toolbox.automations.inbox },
            },
            "。想让它每次都按同一套拆法跑，先去 ",
            {
              text: "工作流",
              link: {
                kind: "jump",
                to: MANUAL_SECTION_IDS.collaboration.workflow,
              },
            },
            " 在工具箱里设计好再绑上。",
          ],
        },
      ],
    },
  ],
};
