import { APP_PATHS } from "../paths";
import { MANUAL_SECTION_IDS } from "../sectionIds";
import type { ManualChapterContent } from "../types";

/** 参考 · 排查 · 信任 —— 结构化内容源（无 JSX）。 */
export const referenceChapter: ManualChapterContent = {
  id: "reference",
  path: APP_PATHS.toolbox.manual.reference,
  label: "参考 · 排查 · 信任",
  sections: [
    {
      id: MANUAL_SECTION_IDS.reference.tools,
      title: "工具与能力",
      icon: "Wrench",
      blocks: [
        {
          type: "lead",
          text: "工具是团队的「手」——读文件、查资料、调外部 API，全靠这些。",
        },
        {
          type: "bullets",
          items: [
            {
              title: "内置工具",
              desc: "平台自带，所有 Agent 开箱即用——读文件、搜索、执行等。",
            },
            {
              title: "白板（画布可用）",
              desc: "工具箱里可自由摆元素；AI 指挥白板即将上线。",
            },
            {
              title: "其他创作工具（即将上线）",
              desc: "文档 / 思维导图 / 表格 / 幻灯片 / 可运行产物——尚未开放。",
            },
            {
              title: "MCP（本机连接器）",
              desc: [
                "在工具箱 ",
                {
                  text: "集成 · 连接器",
                  link: { kind: "go", to: APP_PATHS.toolbox.connectors },
                },
                " 配置本机 stdio MCP Server；启用后 worker 可调用其工具（一律需审批）。仅桌面端；Web / 手机无本地 MCP。",
              ],
            },
            {
              title: "A2A（规划中）",
              desc: "连接外部 Agent 的行业标准协议——尚未开放入口。",
            },
          ],
        },
        {
          type: "callout",
          variant: "tip",
          text: [
            "完整清单在 ",
            {
              text: "工具箱 · 能力图鉴",
              link: { kind: "go", to: APP_PATHS.toolbox.tools },
            },
            "——每个工具能做什么、谁可用，一目了然。",
          ],
        },
        {
          type: "callout",
          variant: "info",
          text: [
            "读写与审批见 ",
            {
              text: "常见问题 · Agent 对 Git",
              link: {
                kind: "jump",
                to: MANUAL_SECTION_IDS.reference.faq,
              },
            },
            "。",
          ],
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.reference.workspace,
      title: "工作区与文件",
      icon: "FolderOpen",
      blocks: [
        {
          type: "lead",
          text: "团队做出来的东西，都落在工作区——你和 AI 共享的文件空间。文件夹即工作区：容器只有文件夹，在「我的文件」里新建（云端），或打开本机文件夹（本地）。",
        },
        {
          type: "bullets",
          items: [
            {
              title: "文件夹即工作区",
              desc: "每个文件夹自带一份工作区，云端或本地在建立时就定下、事后不改绑；文件夹内的对话共用这份空间。",
            },
            {
              title: "打开本机文件夹",
              desc: "打开你电脑上的目录——团队直接改真实文件，适合开发内环；打开过的会留在「本机文件夹」列表里。",
            },
            {
              title: "我的文件",
              desc: "在「我的文件」里新建文件夹，可任意嵌套；文件在服务端，手机、网页看到同一份。随手裸聊则用对话临时空间。",
            },
            {
              title: "模式条",
              desc: "对话页顶部的云/本地指示条告诉你当前在哪跑；可随时看清绑定状态。",
            },
            {
              title: "右坞终端",
              desc: "右侧面板「终端」tab：你的交互 shell、后台进程与执行记录——长任务可观测、可停。",
            },
            {
              title: "右坞浏览器",
              desc: "统一浏览器：桌面可 Local，云端 Sandbox。需要时用「+」或聊天里的入口打开；AI 浏览过程在对话里可见，点开可看直播、接管登录。",
            },
            {
              title: "文件工作台",
              desc: "在文件页直接看、改、整理产物。",
            },
            {
              title: "删了能找回",
              desc: "对话删掉不弹确认，进「最近删除」，保留期内都能恢复：刚删完点提示上的「撤销」，或去「全部对话」页左边的「最近删除」。对话连同全部消息回到原来的位置，但公开分享链接不会一起回来，需要重新分享。文件夹删除仍弹窗；恢复会把它一并归档的对话带回来，白板则留在顶层白板列表。弹窗里勾「立即永久清除」才是不可逆的。本机文件夹在你电脑上的文件，删除与恢复都不会动。",
            },
          ],
        },
        {
          type: "callout",
          variant: "tip",
          text: "想让团队基于某个文件干活？对话里直接引用它就行。",
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.reference.settings,
      title: "设置速查",
      icon: "Settings",
      blocks: [
        {
          type: "lead",
          text: "常用设置入口，点击直达。",
        },
        {
          type: "settingsRows",
          rows: [
            {
              label: "模型",
              desc: "账号默认组合与组合管理",
              to: APP_PATHS.more.model,
            },
            {
              label: "服务商",
              desc: "接入额度或自带 Key（BYOK）",
              to: APP_PATHS.more.providers,
            },
            {
              label: "全局设定",
              desc: "在文件页查看、编辑画像、偏好与规则",
              to: APP_PATHS.files,
            },
            {
              label: "用量",
              desc: "查看花费与额度",
              to: APP_PATHS.more.usage,
            },
            {
              label: "通用",
              desc: "界面主题与进阶开关",
              to: APP_PATHS.more.general,
            },
            {
              label: "快捷键",
              desc: "常用操作的键盘快捷键",
              to: APP_PATHS.more.shortcuts,
            },
            {
              label: "反馈",
              desc: "提 Bug、功能建议或体验改进",
              to: APP_PATHS.more.feedback,
            },
            {
              label: "关于",
              desc: "版本与产品信息",
              to: APP_PATHS.more.about,
            },
          ],
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.reference.faq,
      title: "常见问题",
      icon: "HelpCircle",
      blocks: [
        {
          type: "faq",
          items: [
            {
              q: "为什么没组团？",
              a: [
                {
                  type: "text",
                  text: "CEO 判断这件事一个人答更快，就直接干、不派队员。复杂、可并行、或你明确要求多人时，才会组团。",
                },
              ],
            },
            {
              q: "怎么强制多人干？",
              a: [
                {
                  type: "text",
                  text: [
                    "把协作姿势说进任务里：并行（「分三路同时调研…」）、串行（「先 A 再 B 再 C」）、辩论（「开一场正反辩论」）。细则与例句见 ",
                    {
                      text: "怎么下任务",
                      link: {
                        kind: "jump",
                        to: MANUAL_SECTION_IDS.collaboration.briefing,
                      },
                    },
                    "。",
                  ],
                },
              ],
            },
            {
              q: "检查点怎么答？",
              a: [
                {
                  type: "text",
                  text: [
                    "拍板卡：提交＝带选择继续，取消＝结束本回合。计划复核：继续 / 调整（备注给下游）/ 取消。写文件等工具审批另弹窗，按自主度配方决定问不问。展开见 ",
                    {
                      text: "检查点与审批",
                      link: {
                        kind: "jump",
                        to: MANUAL_SECTION_IDS.collaboration.checkpoint,
                      },
                    },
                    "。",
                  ],
                },
              ],
            },
            {
              q: "跑偏了 / 中途想改方向？",
              a: [
                {
                  type: "text",
                  text: [
                    "发消息纠偏（默认在现有基础上改）；局部不满意可带现场续派唤回原队员；方向全错用重新生成或明说「推翻重来」；太慢就点停止。展开见 ",
                    {
                      text: "中途插手",
                      link: {
                        kind: "jump",
                        to: MANUAL_SECTION_IDS.collaboration.control,
                      },
                    },
                    "。",
                  ],
                },
              ],
            },
            {
              q: "工作流和自动化有什么区别？",
              a: [
                {
                  type: "text",
                  text: [
                    "工作流管「活儿怎么拆」——去工具箱新建或套官方模板，在画布上设计可复用的团队拆法；自动化管「什么时候跑」——给任务配定时或 Webhook，到点由 CEO 自动开一轮。任务绑一张工作流就按图跑，不绑就按目标文案让 CEO 即兴组队。展开见 ",
                    {
                      text: "工作流",
                      link: {
                        kind: "jump",
                        to: MANUAL_SECTION_IDS.collaboration.workflow,
                      },
                    },
                    " 与 ",
                    {
                      text: "自动化",
                      link: {
                        kind: "jump",
                        to: MANUAL_SECTION_IDS.collaboration.automation,
                      },
                    },
                    "。",
                  ],
                },
              ],
            },
            {
              q: "电脑关着，定时任务还会跑吗？",
              a: [
                {
                  type: "text",
                  text: [
                    "会——任务跑在云端，所以只能绑云工作区，本机文件夹选不了。跑完的摘要与待你拍板的挂起项都留在 ",
                    {
                      text: "自动化 · 收件箱",
                      link: {
                        kind: "go",
                        to: APP_PATHS.toolbox.automations.inbox,
                      },
                    },
                    "。",
                  ],
                },
              ],
            },
            {
              q: "画布和白板有什么区别？",
              a: [
                {
                  type: "text",
                  text: "画布是对话里的跨回合空间视图——把多轮协作图画在一张可平移的空间上；白板是工具箱里的独立创作工具，画布可自由摆元素，AI 指挥白板即将上线。",
                },
              ],
            },
            {
              q: "费用怎么看？",
              a: [
                {
                  type: "text",
                  text: [
                    "打开 ",
                    {
                      text: "设置 · 用量",
                      link: { kind: "go", to: APP_PATHS.more.usage },
                    },
                    " 看花费与额度；复杂任务（多队员、更强模型、深度思考）会更贵。",
                  ],
                },
              ],
            },
            {
              q: "怎么给产品提意见？",
              a: [
                {
                  type: "text",
                  text: [
                    "去 ",
                    {
                      text: "设置 · 反馈",
                      link: { kind: "go", to: APP_PATHS.more.feedback },
                    },
                    "，选分类、写标题和描述即可。我们会附带当前页面路由（方便定位你在哪），不含工作区里的文件内容。",
                  ],
                },
              ],
            },
            {
              q: "Agent 对 Git / 代码能做什么？",
              a: [
                {
                  type: "text",
                  text: "三类边界，和审批弹窗一致：",
                },
                {
                  type: "boundaryTable",
                  rows: [
                    {
                      can: "读文件；git status / diff / log / fetch / show / blame；stash/tag/remote list",
                      approve:
                        "改文件；git add / commit / push / pull / 建分支 / 切分支；merge / rebase / cherry-pick；stash push/pop；tag create；remote add；开 PR（GitHub）；跑代码",
                      wont: "force push；reset / clean；stash drop/clear；删 tag；remote remove；在 main / master 上直接提交、push 或 merge/rebase；GitLab 开 PR",
                    },
                  ],
                },
                {
                  type: "text",
                  text: "普通 push / 开 PR 会先弹确认；force / 推保护分支仍禁止。",
                },
              ],
            },
            {
              q: "用的什么模型？",
              a: [
                {
                  type: "text",
                  text: "平台代付，开箱即可对话。",
                },
                {
                  type: "text",
                  text: [
                    "想用自己的模型？自带 Key（BYOK）——在 ",
                    {
                      text: "服务商",
                      link: { kind: "go", to: APP_PATHS.more.providers },
                    },
                    " 接 OpenAI / DeepSeek / Kimi / 智谱 / 豆包 / OpenRouter，或填自定义端点；可同时接多家服务商，在「设置 · 模型」里配组合，聊天框里随时切换。每个回合全链路用你选的那一个模型。",
                  ],
                },
              ],
            },
            {
              q: "数据存哪？",
              a: [
                {
                  type: "text",
                  text: "文件在工作区（本地文件夹或云端空间）；对话记录在后端，用于续聊与记忆。文件页随时看、随时导出。",
                },
              ],
            },
            {
              q: "断网了还能用吗？",
              a: [
                {
                  type: "text",
                  text: "可以浏览已缓存的对话和本机文件（只读）。不能发送消息、不能改文件、不能跑 AI；恢复连接后再继续。本机传统 / 本地引擎 ≠ 离线——推理仍走云端。",
                },
              ],
            },
            {
              q: "接下来会做什么？",
              a: [
                {
                  type: "text",
                  text: [
                    "应用持续迭代。公开方向见产品沟通与 ",
                    {
                      text: "关于",
                      link: { kind: "go", to: APP_PATHS.more.about },
                    },
                    "。",
                  ],
                },
              ],
            },
            {
              q: "想了解底层怎么跑的？",
              a: [
                {
                  type: "text",
                  text: [
                    "看 ",
                    {
                      text: "看懂协作（选读）",
                      link: {
                        kind: "go",
                        to: APP_PATHS.toolbox.manual.mechanism,
                      },
                    },
                    "：先看团队跑一遍（活图），再到图例、「从发消息到收答案」、机制场景——全有。",
                  ],
                },
              ],
            },
          ],
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.reference.troubleshooting,
      title: "故障排查",
      icon: "LifeBuoy",
      blocks: [
        {
          type: "lead",
          text: "卡住了？对症看这里。",
        },
        {
          type: "faq",
          items: [
            {
              q: "填了 Key 还是报错 / 用不了",
              a: [
                {
                  type: "text",
                  text: [
                    "去 ",
                    {
                      text: "设置 · 服务商",
                      link: { kind: "go", to: APP_PATHS.more.providers },
                    },
                    " 核对 Key、base URL 与模型名是否填对；换一家厂商或自定义端点再试，确认是否为 Key 问题。",
                  ],
                },
              ],
            },
            {
              q: "任务一直转、半天不动",
              a: [
                {
                  type: "text",
                  text: "多半卡在某个队员或外部工具。点停止结束本回合（协作图呈「已停止」），或发消息追问状态；长任务可中途打断，下次从断点续跑。",
                },
              ],
            },
            {
              q: "产物找不到 / 没生成文件",
              a: [
                {
                  type: "text",
                  text: "先打开文件页看工作区——Agent 创建、修改的文件都落在那里。「我的文件」换设备也能看到同一份；本机文件夹请确认打开的是你以为的那个目录。",
                },
              ],
            },
            {
              q: "费用涨得比预期快",
              a: [
                {
                  type: "text",
                  text: [
                    "在 ",
                    {
                      text: "设置 · 用量",
                      link: { kind: "go", to: APP_PATHS.more.usage },
                    },
                    " 对明细：多队员并行、更强模型、深度思考都会抬高单次成本。可换更省的模型、少开深度思考，或把大任务拆小后再发。",
                  ],
                },
              ],
            },
          ],
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.reference.privacy,
      title: "数据与隐私",
      icon: "Lock",
      blocks: [
        {
          type: "lead",
          text: "信任边界：你的 Key、文件与对话归你管；平台只为跑通产品所必需而处理。",
        },
        {
          type: "bullets",
          items: [
            {
              title: "自带 Key（BYOK）",
              desc: "平台代付开箱即用；想换模型再自带 API Key（BYOK），在「设置 · 服务商」填写与管理。",
            },
            {
              title: "生成的文件",
              desc: "都在工作区，你可在文件页随时查看、编辑、导出。",
            },
            {
              title: "对话记录",
              desc: "保存在后端，用于续聊与记忆；随时可查。正式说明见「设置 · 关于」中的隐私政策。",
            },
            {
              title: "记忆",
              desc: "团队记住的偏好来自你的对话；想改写或清掉，直接说即可，或在「文件」页的「全局设定」里编辑、清理。",
            },
            {
              title: "反馈附带的上下文",
              desc: "提交反馈时会自动带上当前页面路由（如所在对话），便于复现问题；不含工作区文件内容。",
            },
          ],
        },
        {
          type: "callout",
          variant: "info",
          text: [
            "记忆怎么跨对话延续、越用越懂你——见指挥章 ",
            {
              text: "记忆",
              link: {
                kind: "jump",
                to: MANUAL_SECTION_IDS.collaboration.memory,
              },
            },
            "。想带走数据？文件页可导出工作区里的产物。",
          ],
        },
      ],
    },
    {
      id: MANUAL_SECTION_IDS.reference.glossary,
      title: "术语",
      icon: "BookMarked",
      blocks: [
        {
          type: "lead",
          text: "手册里常出现的词，一句话一个——与产品术语表对齐。",
        },
        {
          type: "faq",
          items: [
            {
              q: "CEO",
              a: [
                {
                  type: "text",
                  text: "主 Agent——本回合对话 + 按需组团 + 收尾汇报。你只跟它对接；用户才是最终决策者。",
                },
              ],
            },
            {
              q: "队员",
              a: [
                {
                  type: "text",
                  text: "被 CEO 委派干某个子任务的 Agent（worker）；干完即走。中文一律称「队员」。",
                },
              ],
            },
            {
              q: "对话",
              a: [
                {
                  type: "text",
                  text: "你与团队的一通聊天单元（对话页 / 对话列表）。中文一律「对话」指这层实体。",
                },
              ],
            },
            {
              q: "会话",
              a: [
                {
                  type: "text",
                  text: "UI「新会话默认」等文案里的「会话」≈ 一次对话（上述「对话」实体）；不是另一套列表。",
                },
              ],
            },
            {
              q: "协作图",
              a: [
                {
                  type: "text",
                  text: "把本次任务的分工、依赖、进度画成的一张实时图。",
                },
              ],
            },
            {
              q: "画布",
              a: [
                {
                  type: "text",
                  text: "对话的跨回合空间视图——多轮协作图累积在一张可平移画布上。≠ 白板。",
                },
              ],
            },
            {
              q: "白板",
              a: [
                {
                  type: "text",
                  text: "工具箱里的独立创作工具——画布可用，自由摆元素；AI 指挥白板即将上线。≠ 画布。",
                },
              ],
            },
            {
              q: "辩论室",
              a: [
                {
                  type: "text",
                  text: "辩论回合的赛事页呈现——记分牌 + 剧本主列 + 终审舞台；入口为状态条「打开辩论室」或全屏「辩论室」tab。",
                },
              ],
            },
            {
              q: "站队",
              a: [
                {
                  type: "text",
                  text: "辩论记分牌上点选你的倾向——仅你可见，绝不改写 AI 裁决；对话内态，重载即重置。",
                },
              ],
            },
            {
              q: "用户检查点",
              a: [
                {
                  type: "text",
                  text: "团队停下来等你拍板的卡片（问答、计划评审、续跑等）——心智是「团队请示领导」。",
                },
              ],
            },
            {
              q: "放行",
              a: [
                {
                  type: "text",
                  text: "审批门放过敏感操作。界面按钮文案是「允许一次 / 本轮内都允许」。",
                },
              ],
            },
            {
              q: "已停止",
              a: [
                {
                  type: "text",
                  text: "你主动喊停（停止生成）后的终态；协作图状态条 / 节点呈「停止 / 已停止」。聊天时间线不另占一行。冷卡次要键「取消」（拒答/拒开工）与此正交，勿混用。",
                },
              ],
            },
            {
              q: "重新生成",
              a: [
                {
                  type: "text",
                  text: "从某条用户消息整轮再跑一遍，要个新答案。改了输入再发叫「调整后重发」；传输失败再试叫「重试」。",
                },
              ],
            },
            {
              q: "带现场续派（同人接续）",
              a: [
                {
                  type: "text",
                  text: "唤回刚干完的同一队员，带着完整现场接着改稿或接强相关新任务——不是新队员从零来。",
                },
              ],
            },
            {
              q: "接续链",
              a: [
                {
                  type: "text",
                  text: "协作图上同一现场根的「续 ×N」节点链；各版并排对比走画布「对比」。有接续标记才是同人，无标记的同角色再委派仍是冷启动新人。",
                },
              ],
            },
            {
              q: "自主度",
              a: [
                {
                  type: "text",
                  text: [
                    "你定团队遇敏感操作时问你多还是少——见 ",
                    {
                      text: "自主度",
                      link: {
                        kind: "jump",
                        to: MANUAL_SECTION_IDS.collaboration.autonomy,
                      },
                    },
                    "。桌面在对话权限徽章选配方后点「设为新会话默认」；手机仍可在设置改。",
                  ],
                },
              ],
            },
            {
              q: "工作区",
              a: [
                {
                  type: "text",
                  text: "你和团队共享的文件空间；文件夹即工作区，产物都落在这里。",
                },
              ],
            },
            {
              q: "工作流",
              a: [
                {
                  type: "text",
                  text: "在工具箱里设计的团队拆法——谁做什么、先后怎么排。可新建空白图，或从官方模板复制一份再改；开跑时再选工作区。官方模板只读，「使用」= 复制一份成你自己的。",
                },
              ],
            },
            {
              q: "系统任务",
              a: [
                {
                  type: "text",
                  text: "自动化页里平台预制的任务（如每日对话复盘）——目标由系统托管、不可改，你只配触发时间、范围与落点。与工作流页的「官方模板」不是一回事。",
                },
              ],
            },
            {
              q: "收件箱",
              a: [
                {
                  type: "text",
                  text: "自动化任务每次运行的结果列表——成功摘要、失败原因、待你拍板的挂起项；tab 红点是还没处理的条数。",
                },
              ],
            },
            {
              q: "允许本机执行",
              a: [
                {
                  type: "text",
                  text: "关于 → 开发者/诊断模式下的选项。开启后，绑定本机文件夹的对话可在本机跑回合（直连磁盘）。这不是离线模式：AI 推理仍走云端。",
                },
              ],
            },
            {
              q: "BYOK",
              a: [
                {
                  type: "text",
                  text: "自带 Key——用你自己的 API Key 调模型。",
                },
              ],
            },
          ],
        },
      ],
    },
  ],
};
