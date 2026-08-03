/**
 * 首页文案单一来源（中英双语）。
 *
 * 组件只负责排版，不内联文案；新增语言时在此处补字段即可。
 * `T` 的两个字段都必填——缺翻译宁可先重复中文，也不要让页面出现空串。
 */
export type Lang = "zh" | "en";

export type T = { zh: string; en: string };

export const t = (v: T, lang: Lang) => v[lang];

/* ── 站点通用 ─────────────────────────────────────────────── */

export const BRAND = "AgentCore";

export const NAV: { href: string; label: T }[] = [
  { href: "#why", label: { zh: "为什么", en: "Why" } },
  { href: "#how", label: { zh: "机制", en: "Mechanism" } },
  { href: "#value", label: { zh: "能力", en: "Capabilities" } },
  { href: "#role", label: { zh: "你的角色", en: "Your role" } },
  { href: "#ecosystem", label: { zh: "生态", en: "Ecosystem" } },
];

export const CTA = {
  webApp: { zh: "立即使用 · 网页版", en: "Open the web app" },
  webAppShort: { zh: "打开网页版", en: "Open the app" },
  desktop: { zh: "下载客户端", en: "Desktop app" },
  mobileWeb: { zh: "手机网页版", en: "Mobile web" },
  mobileStart: { zh: "立即使用 · 手机版", en: "Start on mobile" },
  desktopSite: { zh: "电脑版网站", en: "Desktop site" },
} satisfies Record<string, T>;

/* ── Hero ─────────────────────────────────────────────────── */

export const HERO = {
  eyebrow: {
    zh: "协作智能平台 · Collaborative Intelligence",
    en: "Collaborative intelligence platform",
  },
  titleTop: { zh: "你不是使用者，", en: "You're not a user." },
  titleBottom: { zh: "你是领导者。", en: "You're the lead." },
  lead: {
    zh: "一句话说清目标。CEO 主 Agent 替你组建团队、按依赖分波并行、让 Agent 之间互审辩论——全过程实时可见，你随时介入、随时拍板。",
    en: "Say what you want. A lead agent assembles the team, runs them in parallel waves, and makes them review each other — every decision visible, yours to steer at any moment.",
  },
  leadMobile: {
    zh: "一句话说清目标。CEO 主 Agent 替你组建团队、分波并行、互审辩论——全程可见，你随时介入。",
    en: "Say what you want. A lead agent assembles the team, runs them in parallel waves and makes them review each other — all visible, all yours to steer.",
  },
  specs: [
    { zh: "免安装", en: "NO INSTALL" },
    { zh: "MCP", en: "MCP" },
    { zh: "可私有部署", en: "SELF-HOSTED" },
    { zh: "网页 · 桌面 · 手机", en: "WEB · DESKTOP · MOBILE" },
  ] satisfies T[],
};

/* ── Hero 控制台（一次真实任务的运行日志）─────────────────── */

export const CONSOLE = {
  taskId: "agentcore · task-2749",
  taskIdShort: "task-2749",
  status: "RUNNING",
  logs: [
    {
      time: "09:41:02",
      actor: { zh: "你", en: "YOU" },
      accent: "brand-2" as const,
      text: {
        zh: "做一份 AI 编程工具的竞品分析，要有数据支撑",
        en: "Competitive analysis of AI coding tools — with data.",
      },
      strong: true,
    },
    {
      time: "09:41:04",
      actor: { zh: "CEO", en: "CEO" },
      accent: "primary" as const,
      text: {
        zh: "拆解为 6 个子任务，组建 4 人小队",
        en: "Split into 6 subtasks · assembled a team of 4",
      },
    },
    {
      time: "09:41:09",
      actor: { zh: "A1·A2", en: "A1·A2" },
      accent: "primary" as const,
      text: {
        zh: "检索与采集并行执行（波次 ①）",
        en: "Retrieval + ingest running in parallel (wave ①)",
      },
    },
    {
      time: "09:41:26",
      actor: { zh: "A3", en: "A3" },
      accent: "primary" as const,
      text: {
        zh: "依赖满足，开始趋势建模（波次 ②）",
        en: "Deps met — modelling the trend (wave ②)",
      },
    },
    {
      time: "09:41:48",
      actor: { zh: "A4", en: "A4" },
      accent: "brand-2" as const,
      text: {
        zh: "对 3 处结论提出反驳，打回重做（波次 ③）",
        en: "Challenged 3 conclusions, sent back for rework (wave ③)",
      },
    },
    {
      time: "09:42:11",
      actor: { zh: "CEO", en: "CEO" },
      accent: "primary" as const,
      text: {
        zh: "综合裁决，报告已生成，等你审阅",
        en: "Adjudicated · report ready for your review",
      },
      strong: true,
      caret: true,
    },
  ],
  bars: [
    {
      id: "A1",
      label: { zh: "检索", en: "Search" },
      value: 1,
      accent: "primary" as const,
    },
    {
      id: "A2",
      label: { zh: "采集", en: "Ingest" },
      value: 0.94,
      accent: "primary" as const,
    },
    {
      id: "A3",
      label: { zh: "分析", en: "Analyse" },
      value: 0.68,
      accent: "primary" as const,
    },
    {
      id: "A4",
      label: { zh: "质检", en: "Review" },
      value: 0.35,
      accent: "brand-2" as const,
    },
  ],
};

/* ── 命题 ─────────────────────────────────────────────────── */

export const THESIS = {
  eyebrow: { zh: "命题 · THESIS", en: "Thesis" },
  lines: [
    {
      zh: "文明的突破，从来不是因为某个人变得更聪明，",
      en: "Civilisation never advanced because one person got smarter.",
    },
    {
      zh: "而是因为我们学会了分工与协作。",
      en: "It advanced because we learned to divide the work.",
    },
  ] satisfies T[],
  punch: { zh: "AI 的下一步，也一样。", en: "AI's next step is no different." },
};

/* ── 问题 ─────────────────────────────────────────────────── */

export const WHY = {
  eyebrow: { zh: "问题 · THE PROBLEM", en: "The problem" },
  titleTop: { zh: "一个助手，", en: "One assistant is always" },
  titleBottom: { zh: "永远在自问自答", en: "grading its own homework" },
  lead: {
    zh: "你让它做一份竞品分析——搜资料、写大纲、填内容，全是同一个「人」。没人查漏，没人反驳，没人审结论。它自己出题、自己答、自己打分。",
    en: 'Ask it for a competitive analysis: it searches, outlines and writes — all the same "person". Nobody checks the gaps, nobody argues back, nobody audits the conclusion. It sets the question, answers it, and scores itself.',
  },
  single: {
    kicker: { zh: "单 Agent 助手", en: "Single-agent assistant" },
    title: {
      zh: "一个助手，串起所有事",
      en: "One assistant, everything strung together",
    },
    body: {
      zh: "上下文越堆越长，角色互相打架，过程不可见。",
      en: "Context piles up, roles collide, and the process is a black box.",
    },
  },
  team: {
    kicker: { zh: "AgentCore", en: "AgentCore" },
    title: { zh: "一支团队，各司其职", en: "A team, each on their own piece" },
    body: {
      zh: "每个 Agent 专注一件事，并行推进、彼此互审，你全程看得见。",
      en: "Each agent owns one thing, they run in parallel and review each other, and you watch it happen.",
    },
  },
};

/* ── 协作机制 ─────────────────────────────────────────────── */

export const MECHANISM = {
  eyebrow: { zh: "机制 · MECHANISM", en: "Mechanism" },
  title: {
    zh: "从一句话，到一支团队的产出",
    en: "From one sentence to a team's output",
  },
  steps: [
    {
      idx: "01",
      title: { zh: "你下达目标", en: "You set the goal" },
      body: {
        zh: "用自然语言说清要什么，不用拆步骤，不用写提示词工程。",
        en: "Say what you want in plain language. No task breakdown, no prompt engineering.",
      },
    },
    {
      idx: "02",
      title: { zh: "CEO 组建团队", en: "The lead builds a team" },
      body: {
        zh: "主 Agent 理解任务，按需委派角色与工具，定下依赖关系 DAG。",
        en: "The lead agent reads the task, delegates roles and tools, and fixes the dependency graph.",
      },
    },
    {
      idx: "03",
      title: { zh: "分波并行，互审辩论", en: "Parallel waves, mutual review" },
      body: {
        zh: "调度器按依赖编排波次，Agent 并行执行、共享工作区、彼此协商互审。",
        en: "The scheduler orders waves by dependency. Agents run in parallel, share one workspace, and argue.",
      },
    },
    {
      idx: "04",
      title: { zh: "你审阅拍板", en: "You review and decide" },
      body: {
        zh: "全过程实时可见，随时介入、采纳或调整，最终决策在你手里。",
        en: "Everything stays visible. Step in, accept, adjust — the final call is yours.",
      },
    },
  ],
  legend: {
    flow: { zh: "任务流向", en: "TASK FLOW" },
    debate: { zh: "协商辩论", en: "DEBATE" },
    waves: {
      zh: "波次 ① 并行 · ② 依赖 · ③ 辩论",
      en: "WAVE ① PARALLEL · ② DEPENDENT · ③ DEBATE",
    },
  },
  nodes: {
    you: { zh: "你", en: "You" },
    ceo: { zh: "CEO 主 Agent", en: "Lead agent" },
    w1a: { zh: "资料检索", en: "Retrieval" },
    w1b: { zh: "数据采集", en: "Data ingest" },
    w2a: { zh: "数据分析", en: "Analysis" },
    w2b: { zh: "趋势研判", en: "Trend read" },
    w3a: { zh: "方案 · 正", en: "For" },
    w3b: { zh: "方案 · 反", en: "Against" },
    fin: { zh: "综合裁决", en: "Adjudicate" },
  } satisfies Record<string, T>,
};

/* ── 核心能力 ─────────────────────────────────────────────── */

export const CAPABILITIES = {
  eyebrow: { zh: "核心能力 · CAPABILITIES", en: "Capabilities" },
  title: {
    zh: "多 Agent 编排 · 全程可见 · 你是领导者",
    en: "Multi-agent orchestration · fully visible · you in charge",
  },
  cards: [
    {
      idx: "01",
      title: { zh: "多 Agent 协作", en: "Multi-agent collaboration" },
      body: {
        zh: "一句需求，CEO 主 Agent 自动组建团队，按依赖分波推进——串行、并行、辩论、互审，统一编排。不是一个助手分饰多角。",
        en: "One request, and the lead agent assembles a team, ordering it into waves by dependency — serial, parallel, debate, review, all under one orchestrator. Not one assistant playing every role.",
      },
    },
    {
      idx: "02",
      title: { zh: "全程可见", en: "Visible end to end" },
      body: {
        zh: "谁在做什么、为什么这样决策、调用了哪些工具、花了多少成本，全部实时可见。协作不再是黑箱，而是一张你看得懂的作战图。",
        en: "Who is doing what, why they decided that way, which tools they called, what it cost — live. Collaboration stops being a black box and becomes a map you can read.",
      },
    },
    {
      idx: "03",
      title: { zh: "你是领导者", en: "You lead" },
      body: {
        zh: "你不再绞尽脑汁写提示词去「使唤」一个工具。像管理团队一样下达目标、审阅产出、随时介入，做最终决策。",
        en: "Stop racking your brain for the prompt that makes a tool behave. Set the goal, review the output, step in whenever you want, and make the final call.",
      },
    },
  ],
  tags: [
    { zh: "多轮对话", en: "Multi-turn dialogue" },
    { zh: "多 Agent 执行", en: "Multi-agent execution" },
    { zh: "动态角色分配", en: "Dynamic role assignment" },
    { zh: "工具调用", en: "Tool calling" },
    { zh: "进度可视化", en: "Progress visualisation" },
    { zh: "跨会话记忆", en: "Cross-session memory" },
    { zh: "成本可见", en: "Cost visibility" },
  ] satisfies T[],
};

/* ── 你的角色 ─────────────────────────────────────────────── */

export const ROLE = {
  eyebrow: { zh: "你的角色 · YOUR ROLE", en: "Your role" },
  title: { zh: "从提示者，到领导者", en: "From prompter to lead" },
  lead: {
    zh: "用 ChatGPT，你得学会「怎么问」。用 AgentCore，你只需要知道「要什么」。",
    en: "With ChatGPT you have to learn how to ask. With AgentCore you only need to know what you want.",
  },
  stages: [
    {
      product: "ChatGPT / Claude",
      name: { zh: "提示者", en: "Prompter" },
      body: { zh: "你得学会「怎么问」。", en: "You have to work out how to ask." },
      tone: "past" as const,
    },
    {
      product: "Cursor / Codex",
      name: { zh: "指令者", en: "Commander" },
      body: {
        zh: "你还得说清「怎么做」。",
        en: "You still have to spell out how it gets done.",
      },
      tone: "mid" as const,
    },
    {
      product: "AgentCore",
      name: { zh: "领导者", en: "Leader" },
      body: {
        zh: "你只需要知道「要什么」。",
        en: "You only need to know what you want.",
      },
      tone: "now" as const,
    },
  ],
};

/* ── 对比 ─────────────────────────────────────────────────── */

export const COMPARE = {
  eyebrow: { zh: "对比 · COMPARISON", en: "Comparison" },
  title: {
    zh: "架构不同，结果就不同",
    en: "Different architecture, different result",
  },
  headOthers: { zh: "单 Agent 助手", en: "Single-agent assistant" },
  headOurs: "AgentCore",
  rows: [
    {
      dim: { zh: "怎么干活", en: "How work happens" },
      others: {
        zh: "一个助手把所有事串着做，前后全靠它一个人",
        en: "One assistant does everything back to back, start to finish.",
      },
      ours: {
        zh: "一支团队各干各的，能同时推进、按依赖关系交接",
        en: "A team splits the work, runs concurrently, hands off by dependency.",
      },
    },
    {
      dim: { zh: "有人把关吗", en: "Who checks it" },
      others: {
        zh: "自己出结果，没人复查，结论未经论证",
        en: "It produces its own answer; nothing is reviewed or argued.",
      },
      ours: {
        zh: "Agent 之间互审辩论，有「质检」环节",
        en: "Agents review and debate each other — there is a QA step.",
      },
    },
    {
      dim: { zh: "你看得见吗", en: "Can you see it" },
      others: {
        zh: "只见最终结果，中间决策是黑箱",
        en: "Only the final answer; the reasoning in between is a black box.",
      },
      ours: {
        zh: "谁在做什么、为什么这样决策，实时可见",
        en: "Who does what and why — visible in real time.",
      },
    },
    {
      dim: { zh: "能扩展吗", en: "Does it scale" },
      others: {
        zh: "配一个 Agent 的提示词和工具",
        en: "You tune one agent's prompt and tools.",
      },
      ours: {
        zh: "工具 / 技能 / 规则 / Agent / 团队，五类资产可沉淀复用",
        en: "Tools, skills, rules, agents, teams — five reusable asset types.",
      },
    },
  ],
};

/* ── 扩展生态 ─────────────────────────────────────────────── */

export const ECOSYSTEM = {
  eyebrow: { zh: "扩展生态 · ECOSYSTEM", en: "Ecosystem" },
  badge: { zh: "即将开放", en: "COMING SOON" },
  title: {
    zh: "不止一个 Agent，而是一整套可沉淀的资产",
    en: "Not one agent — a whole stack you can keep",
  },
  lead: {
    zh: "把你打磨好的工作流和团队，沉淀成五类可复用的资产——自己用，或分享给别人。",
    en: "Turn the workflows and teams you have tuned into five kinds of reusable assets — for yourself, or to share.",
  },
  assets: [
    {
      code: "Tool",
      name: { zh: "工具", en: "Tool" },
      body: {
        zh: "接入你的数据库、API、文件系统",
        en: "Connect your database, APIs, file system.",
      },
      accent: "primary" as const,
    },
    {
      code: "Skill",
      name: { zh: "技能", en: "Skill" },
      body: {
        zh: "沉淀你的分析流程、写作规范",
        en: "Bank your analysis flow and writing standards.",
      },
      accent: "primary" as const,
    },
    {
      code: "Rule",
      name: { zh: "规则", en: "Rule" },
      body: {
        zh: "团队行为的红线与偏好",
        en: "The red lines and preferences your team follows.",
      },
      accent: "brand-2" as const,
    },
    {
      code: "Agent",
      name: { zh: "队员", en: "Agent" },
      body: {
        zh: "配好角色的专精队员",
        en: "A specialist with its role already configured.",
      },
      accent: "brand-2" as const,
    },
    {
      code: "Team",
      name: { zh: "团队", en: "Team" },
      body: {
        zh: "一键复用整支团队配置",
        en: "Reuse an entire team configuration in one click.",
      },
      accent: "primary" as const,
      featured: true,
    },
  ],
};

/* ── 结尾 CTA ─────────────────────────────────────────────── */

export const CLOSING = {
  title: {
    zh: "协作，是更高级的智能。",
    en: "Collaboration is the higher intelligence.",
  },
  lead: {
    zh: "AgentCore —— 让 AI 像团队一样工作。",
    en: "AgentCore — make AI work like a team.",
  },
};

/* ── 页脚 ─────────────────────────────────────────────────── */

export const FOOTER = {
  blurb: {
    zh: "协作智能平台。让多个 AI Agent 像团队一样分工、协商、互审，共同完成复杂任务。",
    en: "A collaborative intelligence platform. Multiple AI agents divide the work, negotiate and review each other to finish complex tasks together.",
  },
  colProduct: { zh: "产品", en: "Product" },
  colLearn: { zh: "了解", en: "Learn" },
  colAbout: { zh: "关于", en: "About" },
  learn: [
    { href: "#how", label: { zh: "协作机制", en: "Mechanism" } },
    { href: "#value", label: { zh: "核心能力", en: "Capabilities" } },
    { href: "#compare", label: { zh: "产品对比", en: "Comparison" } },
  ],
  about: [
    { href: "#thesis", label: { zh: "核心理念", en: "Our thesis" } },
    { href: "#ecosystem", label: { zh: "扩展生态", en: "Ecosystem" } },
  ],
  aboutNote: {
    zh: "面向大众的 Multi-Agent 工作台",
    en: "A Multi-Agent workbench for everyone",
  },
  copyright: {
    zh: "© 2026 AgentCore · 协作智能平台",
    en: "© 2026 AgentCore · Collaborative Intelligence Platform",
  },
  stack: "MCP · WEB · DESKTOP · MOBILE",
};
