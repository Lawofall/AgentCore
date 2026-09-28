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
              desc: ["平台自带。查阅在工具箱「官方」。"],
            },
            {
              title: "MCP",
              desc: [
                "在工具箱顶栏 ",
                {
                  text: "MCP",
                  link: { kind: "go", to: APP_PATHS.toolbox.mcp },
                },
                " 配置本机 stdio MCP Server；启用后团队可调用（一律先问你）。仅桌面；Web / 手机无本地 MCP。",
              ],
            },
            {
              title: "市场",
              desc: [
                "工具箱顶栏 ",
                {
                  text: "市场",
                  link: { kind: "go", to: APP_PATHS.toolbox.market },
                },
                "：安装＝复制进提示词目录。",
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
              text: "工具箱",
              link: { kind: "go", to: APP_PATHS.toolbox.mine.skills },
            },
            "——每个工具能做什么、谁可用，一目了然。手机 / 窄屏无工具箱，无此入口。",
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
          text: "团队做出来的东西，都落在你的文件夹里。文件夹即工作区：容器只有文件夹，在「我的文件」里新建（云端），或打开本机文件夹（本地）。右坞「工作区」是本对话的文件树，不是另一种容器。",
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
              desc: "云端文件夹在这里；新建云桌走对话里的「新建或加入」。文件在服务端，手机、网页看到同一份。随手裸聊则用对话临时空间。",
            },
            {
              title: "协作桌",
              desc: "在自己的云文件夹上邀请成员。对方在「与我共享」看见这张桌，可开聊、看桌上全部对话；右坞仍是同一棵文件夹树。本机文件夹不分享。",
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
              desc: "统一浏览器：桌面可 Local，云端 Sandbox。需要时用「+」或聊天里的入口打开；AI 浏览过程在对话里可见，点开可看直播；需要登录时在右坞完成登录。",
            },
            {
              title: "文件工作台",
              desc: "在文件页直接看、改、整理产物。",
            },
            {
              title: "删了能找回",
              desc: "对话删掉不弹确认，进「最近删除」，约 30 天内都能恢复：刚删完点提示上的「撤销」，或去「全部对话」页左边的「最近删除」。对话连同全部消息回到原来的位置，但公开分享链接不会一起回来，需要重新分享。进「最近删除」后也可以彻底删除（再确认一次）。文件夹删除仍弹窗：默认删后该文件夹从侧栏消失、其下对话一并归档（在「已归档」里仍能找到），这张桌的 AI 设定不再带进对话；云端文件约 30 天后由系统自动清理。这段时间内可以找回——删完提示上点「撤销」，或到「最近删除」恢复（文件夹、归档的对话和这张桌的设定一起回来）。勾「立即永久删除」或之后彻底删除，才连设定一起不可恢复。两种删法都不动你电脑上的文件。",
            },
            {
              title: ".md 阅读预览",
              desc: "点工作区里的 .md 在文件面板内阅读（不是语法教程）。",
            },
            {
              title: "HTML「完整预览」（仅桌面）",
              desc: "点终稿里的 .html 或文件横幅「完整预览」，在右坞浏览器里看跑 JS 的完整效果；与 .md 阅读预览不是一路。Web / 手机无此按钮，出口是文件面板下载。",
            },
            {
              title: "云上文件拿到电脑",
              desc: "桌面对话顶栏「合回到本机」（首次选落点，冲突默认留你电脑上已有的文件）。Web / 手机无「合回到本机」，走文件面板逐个下载。若打开的就是本机文件夹，文件已经在那台电脑上。没有整份文件的公开下载网址；分享对话只带问答，不带文件。",
            },
            {
              title: "分享这场对话",
              desc: "对话行右键 / ⋯「分享…」（桌面也可用命令面板「分享当前对话」）创建只读公开链接：分享当时的问答快照，之后新消息不会出现；有效期 7 天 / 30 天 / 永久，可随时撤销。删对话后原链接不会自动回来，需重新分享。",
            },
          ],
        },
        {
          type: "callout",
          variant: "warning",
          text: "云端文件不在你电脑上：用合回或文件面板下载拿走。打开的是本机文件夹时，才是电脑上的真实路径。",
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
          text: "常用设置入口，点击直达。桌面「设置 · 用量」/「设置 · 模型组合」/「设置 · 服务商」在侧栏；手机 ☰ 打开侧栏进「设置」再点「用量」、再点「模型组合」、再点「服务商」。手机无 Git 凭据、通用、快捷键入口。",
        },
        {
          type: "settingsRows",
          rows: [
            {
              label: "模型组合",
              desc: "账号默认组合与组合管理",
              to: APP_PATHS.more.model,
            },
            {
              label: "服务商",
              desc: "接入额度或自带 Key（BYOK）",
              to: APP_PATHS.more.providers,
            },
            {
              label: "提示词",
              desc: "所有对话共用的提示词，在工具箱查看与调整",
              to: APP_PATHS.toolbox.guidelines,
            },
            {
              label: "用量",
              desc: "查看花费与额度",
              to: APP_PATHS.more.usage,
            },
            {
              label: "通用",
              desc: "界面主题与联网搜索",
              to: APP_PATHS.more.general,
            },
            {
              label: "快捷键",
              desc: "常用操作的键盘快捷键",
              to: APP_PATHS.more.shortcuts,
            },
            {
              label: "关于",
              desc: "版本、产品手册与法律信息",
              to: APP_PATHS.more.about,
            },
            {
              label: "赞助",
              desc: "如果本产品对你有所帮助，欢迎您的慷慨赞助支持创作。",
              to: APP_PATHS.more.sponsor,
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
                  text: "CEO 判断这件事一个人答更快，就直接干、不派队员。要并行、独立验证、成篇，或你明确要求多人时，才会组团。",
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
                    "拍板卡：提交＝带选择继续，取消＝结束本回合。装软件、推远程、毁灭形删除、读私钥仍会单独拦住；边界内的改文件和跑命令不再逐次问。展开见 ",
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
                    "团队还在跑时直接发送马上给主 Agent；要等收工后再说点「排队」。只改某一人走详情「立即改此人」。局部不满意可带现场续派唤回原队员；方向全错用重新生成或明说「推翻重来」；太慢就点停止。展开见 ",
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
              q: "为什么老弹审批 / 边界怎么选？",
              a: [
                {
                  type: "text",
                  text: "对话输入区权限徽章三档：只看＝只能看和搜索；这个文件夹（推荐）＝这个文件夹里可以改文件和跑命令；这台电脑＝仅桌面，还能做本机操作。装软件、推远程、毁灭形删除、读私钥仍会单独拦住。选完可点「设为新会话默认」（只影响之后新建的对话）。",
                },
              ],
            },
            {
              q: "怎么装提示词 / 市场在哪？",
              a: [
                {
                  type: "text",
                  text: "仅桌面：工具箱顶栏「市场」。安装＝复制进「我的」。手机无工具箱。",
                },
              ],
            },
            {
              q: "写的提示词怎么才会被用上？",
              a: [
                {
                  type: "text",
                  text: [
                    "按需那一句要写清干什么、什么时候翻开；空着 CEO 不会主动翻，当场用就 @。展开见 ",
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
              q: "MCP 在哪？",
              a: [
                {
                  type: "text",
                  text: "仅桌面：工具箱顶栏「MCP」接本机 Server；启用后团队可调用（一律先问你）。Web / 手机无本地 MCP。",
                },
              ],
            },
            {
              q: "协作桌怎么邀请？",
              a: [
                {
                  type: "text",
                  text: "自己的云文件夹右键 / ⋯「成员」→ 邀请；对方在文件页 / 侧栏「与我共享」看见这张桌，可开聊、看桌上对话。本机文件夹不能邀请。这不是公开分享链接。",
                },
              ],
            },
            {
              q: "云上做完的文件怎么拿到电脑？",
              a: [
                {
                  type: "text",
                  text: "桌面：对话顶栏「合回到本机」。Web / 手机走文件面板下载。若打开的就是本机文件夹，文件已经在那台电脑上。没有整份文件的公开下载网址；分享对话只带问答，不带文件。",
                },
              ],
            },
            {
              q: "怎么分享这场对话？",
              a: [
                {
                  type: "text",
                  text: "对话行右键 / ⋯「分享…」（桌面也可用命令面板「分享当前对话」）创建只读公开链接：分享当时的问答快照，之后新消息不会出现；有效期 7 天 / 30 天 / 永久，可随时撤销。",
                },
              ],
            },
            {
              q: "删对话能找回吗？",
              a: [
                {
                  type: "text",
                  text: "能，约 30 天内：删完那条提示上点「撤销」，或到「全部对话」页左边「最近删除」里恢复。带不回来的只有删除时已撤销的公开分享链接。进「最近删除」后也可以彻底删除（再确认一次），不可恢复。",
                },
              ],
            },
            {
              q: "删文件夹会怎样？能找回吗？",
              a: [
                {
                  type: "text",
                  text: "右键侧栏文件夹（或文件页文件夹根）→「删除文件夹…」。默认删：文件夹从侧栏消失、其下对话一并归档（在「已归档」里仍能找到），这张桌的 AI 设定不再带进对话；云端文件约 30 天后由系统自动清理。这段时间内可以找回——删完提示上点「撤销」，或到「最近删除」恢复（文件夹、归档的对话和这张桌的设定一起带回来）。勾「立即永久删除」立刻不可恢复。两种删法都不动你电脑上的文件。",
                },
              ],
            },
            {
              q: "Cursor 规则和 AgentCore 规矩是一回事吗？",
              a: [
                {
                  type: "text",
                  text: "不是。Cursor 的 .cursor/rules 不是本产品的用户规矩。本产品的规矩来自你说的「记住」和提示词页；技能包也不是把 Cursor 规则搬过来的地方。",
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
                    " 看花费与额度；复杂任务（多队员、更强模型、更高思考强度）会更贵。手机 ☰ 打开侧栏进「设置」再点「用量」。想把思考强度调低：桌面「设置 · 模型组合」/ 手机同路径把思考强度调到 low。",
                  ],
                },
              ],
            },
            {
              q: "怎么给产品提意见？",
              a: [
                {
                  type: "text",
                  text: "讨论请去消息页内测群；意见与投诉请走官网 https://fashitianxia.xyz。",
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
                      can: "读文件；git status / diff / log / fetch",
                      approve:
                        "改文件；git add / commit / push / pull / 建分支 / 切分支；开 PR（GitHub）；跑代码",
                      wont: "force push；reset / clean；在 main / master 上直接提交或 push；GitLab 开 PR",
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
                    " 接 OpenAI / DeepSeek / Kimi / 智谱 / 豆包 / OpenRouter，或填自定义端点；可同时接多家服务商，在 ",
                    {
                      text: "设置 · 模型组合",
                      link: { kind: "go", to: APP_PATHS.more.model },
                    },
                    " 里配组合，聊天框里随时切换。每个回合全链路用你选的那一个模型。桌面接入在「设置 · 服务商」、组合在「设置 · 模型组合」；手机 ☰ 打开侧栏进「设置」再点「服务商」接入、再点「模型组合」改组合。",
                  ],
                },
              ],
            },
            {
              q: "数据存哪？",
              a: [
                {
                  type: "text",
                  text: "文件在你的文件夹里（本机文件夹、「我的文件」，或别人邀请你的协作桌「与我共享」）；对话记录在后端，用于续聊。过往事情可以查旧对话。文件页随时看、随时导出。",
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
              q: "是不是产品坏了 / 要上报？",
              a: [
                {
                  type: "text",
                  text: "讨论请去消息页内测群；意见与投诉请走官网公示渠道 https://fashitianxia.xyz。请不要改产品仓或开 PR。",
                },
              ],
            },
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
                    " 核对 Key、base URL 与模型名是否填对；换一家厂商或自定义端点再试，确认是否为 Key 问题。手机 ☰ 打开侧栏进「设置」再点「服务商」。",
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
                  text: "先打开文件页——Agent 创建、修改的文件都落在你的文件夹里。「我的文件」换设备也能看到同一份；本机文件夹请确认打开的是你以为的那个目录。",
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
                    " 对明细：多队员并行、更强模型、更高思考强度都会抬高单次成本。可换更省的模型、到 ",
                    {
                      text: "设置 · 模型组合",
                      link: { kind: "go", to: APP_PATHS.more.model },
                    },
                    " 把思考强度调到 low，或把大任务拆小后再发。",
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
              desc: "都在你的文件夹里，可在文件页随时查看、编辑、导出。",
            },
            {
              title: "对话记录",
              desc: "保存在后端，用于续聊；过往事情可以查。正式说明见「设置 · 关于」中的隐私政策。",
            },
            {
              title: "规矩与旧对话",
              desc: "你写下的规矩会跨对话生效；过往事情查旧对话。想改规矩直接说即可，或在工具箱的提示词页查看、调整。",
            },
          ],
        },
        {
          type: "callout",
          variant: "info",
          text: [
            "规矩怎么跨对话生效、过往事情怎么查——见指挥章 ",
            {
              text: "规矩与旧对话",
              link: {
                kind: "jump",
                to: MANUAL_SECTION_IDS.collaboration.memory,
              },
            },
            "。想带走数据？文件页可导出文件夹里的产物。",
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
                  text: "对话的跨回合空间视图——多轮协作图累积在一张可平移画布上。",
                },
              ],
            },
            {
              q: "文档",
              a: [
                {
                  type: "text",
                  text: "工具箱里可反复打开的长文，挂在云文件夹上。≠ 规则树。",
                },
              ],
            },
            {
              q: "辩论室",
              a: [
                {
                  type: "text",
                  text: "辩论回合的赛事页呈现——顶栏 + 剧本主列 + 终审舞台；入口为状态条「打开辩论室」或全屏「辩论室」tab。",
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
                  text: "你主动喊停（停止生成）后的终态；协作图状态条 / 节点呈「停止 / 已停止」。聊天时间线不另占一行。冷卡次要键「取消」（拒答）与此正交，勿混用。",
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
                  text: "同一现场根在图上仍是一个座位（可挂「续 ×N」角标）；各版在右坞往下接，不另开节点。有接续标记才是同人，无标记的同角色再委派仍是冷启动新人。辩论轮次仍分列。",
                },
              ],
            },
            {
              q: "对话边界",
              a: [
                {
                  type: "text",
                  text: [
                    "这一场能改文件、跑命令、动这台电脑到哪一步——见 ",
                    {
                      text: "对话边界",
                      link: {
                        kind: "jump",
                        to: MANUAL_SECTION_IDS.collaboration.autonomy,
                      },
                    },
                    "。在对话权限徽章里选，点「设为新会话默认」只影响之后新建的对话。",
                  ],
                },
              ],
            },
            {
              q: "工作区",
              a: [
                {
                  type: "text",
                  text: "右坞「工作区」是本对话的文件树（寻址铬条），不是另一种容器。产物落在文件夹里——「我的文件」或本机文件夹。",
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
