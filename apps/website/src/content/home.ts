/**
 * 首页文案单一来源（中英双语）。
 *
 * 组件只负责排版，不内联文案；新增语言时在此处补字段即可。
 * `T` 的两个字段都必填——缺翻译宁可先重复中文，也不要让页面出现空串。
 */
export type Lang = "zh" | "en";

export type T = { zh: string; en: string };

export const t = (v: T, lang: Lang) => v[lang];

/*
 * 标题里的「点睛词」用行内标记表达，而不是把句子拆成 pre/accent/post 三段——
 * 因为强调词在中英文里落在句子的不同位置，拆段会逼着译文迁就结构。
 *
 *   [[词]]  → 衬线斜体 + 渐变色（中文回落宋体，不倾斜）
 *   {{词}}  → 手绘波浪下划线
 *   \n      → 强制换行
 *
 * 渲染见 components/RichTitle.tsx。
 */

/* ── 站点通用 ─────────────────────────────────────────────── */

export const BRAND = "AgentCore";

export const NAV: { href: string; label: T }[] = [
  { href: "#build", label: { zh: "能做什么", en: "What it ships" } },
  { href: "#how", label: { zh: "机制", en: "Mechanism" } },
  { href: "#value", label: { zh: "能力", en: "Capabilities" } },
  { href: "#role", label: { zh: "你的角色", en: "Your role" } },
  { href: "#ecosystem", label: { zh: "生态", en: "Ecosystem" } },
];

export const CTA = {
  webApp: { zh: "立即使用 · 网页版", en: "Open the web app" },
  webAppShort: { zh: "打开网页版", en: "Open the app" },
  desktop: { zh: "下载客户端", en: "Desktop app" },
  desktopSite: { zh: "电脑版网站", en: "Desktop site" },
  backHome: { zh: "返回首页", en: "Back to site" },
} satisfies Record<string, T>;

/* ── Hero ─────────────────────────────────────────────────── */

export const HERO = {
  eyebrow: {
    zh: "协作智能平台 · Collaborative Intelligence",
    en: "Collaborative intelligence platform",
  },
  titleTop: { zh: "你不是使用者，", en: "You're not a user." },
  titleBottom: { zh: "你是领导者。", en: "You're the lead." },
  /** 首屏大标题（带点睛词标记，见文件头说明）。 */
  headline: {
    zh: "[[协作]]，\n是更高级的智能",
    en: "[[Collaboration]]\nis the higher intelligence",
  },
  /*
   * 首屏引文块，替代原来那段功能说明。
   * 注意：这两句原本在 THESIS 里，标题原本在 CLOSING 里——搬上首屏后
   * 那两处都已改写，别再把旧句子放回去，否则一页三遍。
   */
  quote: {
    zh: "人类文明的突破不是因为某个人变得更聪明，\n而是因为我们学会了分工与协作。",
    en: "Civilisation never advanced because one person got smarter,\nbut because we learned to divide the work.",
  },
  punch: {
    zh: "AI 的下一步，不是更聪明的个体，而是[[更好的协作]]。",
    en: "AI's next step isn't a smarter individual — it's [[better collaboration]].",
  },
  lead: {
    zh: "一句话说清目标。CEO 主 Agent 替你组建团队、按依赖分波并行、让 Agent 之间互审辩论——全过程实时可见，你随时介入、随时拍板。",
    en: "Say what you want. A lead agent assembles the team, runs them in parallel waves, and makes them review each other — every decision visible, yours to steer at any moment.",
  },
  leadMobile: {
    zh: "一句话说清目标。CEO 主 Agent 替你组建团队、分波并行、互审辩论——全程可见，你随时介入。",
    en: "Say what you want. A lead agent assembles the team, runs them in parallel waves and makes them review each other — all visible, all yours to steer.",
  },
  /** 首屏 CTA 下的平台一句：怎么上手，不抢引文。 */
  platform: {
    zh: "免安装 · 网页 · 桌面 · 手机",
    en: "No install · Web · Desktop · Mobile",
  },
};

/* ── Hero 协作图（复刻产品内「协作图」画布）───────────────── */

export const GRAPH = {
  toolbarTitle: { zh: "5 个 worker · 按依赖分波", en: "5 workers · dependency waves" },
  toolbarStop: { zh: "停止", en: "Stop" },
  toolbarView: { zh: "协作图", en: "Graph" },

  task: {
    title: { zh: "你的任务", en: "Your task" },
    sub: { zh: "对话发起", en: "From chat" },
  },

  thinking: { zh: "思考中", en: "Thinking" },
  finished: { zh: "已完成", en: "Done" },
  queued: { zh: "等待依赖", en: "Waiting on deps" },

  /* 五个 worker、三个波次（与 CollabGraph 时间线 / 顶栏人数必须一致）。
     呈现是扇出 + 时序错开，不必画 DAG 边；但角色与波次仍按依赖讲：
     ①两个并行摸底 → ②吃①产出 → ③质检进场。若五条同时亮同时灭，
     看起来就只是「一个助手分饰五角」，正是本站要反的那件事。 */
  workers: [
    {
      name: { zh: "资料检索", en: "Retrieval" },
      wave: "①",
      tool: "Search web",
      note: {
        zh: 'web_search: "国内纸袋 市场规模 2023-2026"',
        en: 'web_search: "paper bag market size 2023-2026"',
      },
    },
    {
      /* EN 名字必须短：分波几何下卡宽只有 0.176·vw，"Data ingest" 会被截成 "Data ing…"。 */
      name: { zh: "数据采集", en: "Ingest" },
      wave: "①",
      tool: "Fetch page",
      note: {
        zh: "fetch: 头部纸袋企业 产能与市占率",
        en: "fetch: leading makers, capacity & share",
      },
    },
    {
      name: { zh: "数据分析", en: "Analysis" },
      wave: "②",
      tool: "Run code",
      note: {
        zh: "python: 拟合 2023-2026 复合增长率",
        en: "python: fit CAGR across 2023-2026",
      },
    },
    {
      name: { zh: "趋势研判", en: "Trend read" },
      wave: "②",
      tool: "Read files",
      note: {
        zh: "read_notes: 限塑令/禁塑政策 时间线梳理",
        en: "read_notes: plastic-ban policy timeline",
      },
    },
    {
      name: { zh: "质检复核", en: "Review" },
      wave: "③",
      tool: "Challenge",
      note: {
        zh: "对 3 处结论提出反驳，打回重做",
        en: "challenged 3 conclusions, sent back",
      },
    },
  ],

  ceo: {
    title: { zh: "CEO 汇总", en: "CEO merge" },
    /* 必须短：状态行拼成「等待 0/5 · 1s」，CEO 卡宽下「等待团队」会折行。 */
    waiting: { zh: "等待", en: "Waiting" },
    merging: { zh: "汇总中", en: "Merging" },
    ready: { zh: "报告已生成", en: "Report ready" },
    body: {
      zh: "编排：①并行摸底 → ②依赖起步 → ③质检收口。",
      en: "Plan: ① parallel scope → ② run on ① → ③ review closes.",
    },
  },
};

/* ── 命题 ─────────────────────────────────────────────────── */

export const THESIS = {
  eyebrow: { zh: "为什么是团队 · WHY A TEAM", en: "Why a team" },
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
  headline: {
    zh: "同一个模型，\n换一种[[组织方式]]",
    en: "Same model,\na different [[way to organise]]",
  },
  lead: {
    zh: "能力上限没变，变的是分工、审查与推进方式——这三件事决定了它能不能啃下真正复杂的任务。",
    en: "The ceiling didn't move. What changed is how work is split, checked and pushed forward — and that decides whether hard tasks land.",
  },
  /** 命题面板的三条支撑，对应 amphora 那一屏的三栏价值主张。 */
  values: [
    {
      title: { zh: "分工，而非[[分身]]", en: "Split the work, not the [[persona]]" },
      body: {
        zh: "每个 Agent 只专注一件事，CEO 按依赖把它们编排成波次并行推进。",
        en: "Each agent owns one thing. The lead orders them into parallel waves by dependency.",
      },
    },
    {
      title: { zh: "不只是[[更快]]", en: "Not just [[faster]]" },
      body: {
        zh: "Agent 之间互审、反驳、打回重做，结论要经得起质疑才算成立。",
        en: "Agents review, challenge and send work back. A conclusion only stands if it survives.",
      },
    },
    {
      title: { zh: "为真正[[复杂]]的任务", en: "For work that is actually [[complex]]" },
      body: {
        zh: "竞品分析、调研报告、多轮方案推演——不是一问一答能解决的那类事。",
        en: "Competitive analysis, research reports, multi-round proposals — not one-shot Q&A.",
      },
    },
  ],
};

/* ── 跑马灯（白纸分区）─────────────────────────────────────── */

/*
 * 支持的模型。
 *
 * 三处真相来源，改这里之前先对一遍：
 *   1. apps/server/agentcore/llm/pricing_data/community_prices.json —— 价卡目录，
 *      43 个模型，是模型 ID 的权威全集（as_of 2026-07-15）；
 *   2. apps/server/agentcore/llm/factory.py::_VENDOR_PROVIDERS —— 前缀路由厂商；
 *   3. apps/desktop/src/renderer/lib/byokProviderPresets.ts —— 桌面端 BYOK 预设。
 *
 * docs/05-平台与运维/平台LLM接入.md 定了一条铁律：**缺 curated 价卡的 id 不上架**。
 * 所以往 top 里加厂商前，先确认价卡目录里有它——否则这一屏就是一句没兑现的承诺。
 *
 * bottom 行的 ID 全部逐条来自上面 1 / 3，一个都不是凭印象写的。
 */
export type Vendor = {
  name: string;
  /** 对应 public/logos/{slug}.svg；文件不存在时降级成字标。 */
  slug: string;
  /** 放进 public/logos/ 后把这里改成 true，即可从字标切到真 logo。 */
  hasLogo?: boolean;
};

export const MARQUEE = {
  eyebrow: {
    zh: "主流大模型，都能接",
    en: "EVERY MAJOR MODEL, PLUGGED IN",
  },

  /*
   * Logo 墙的两行。组件按 rowSplit 切分，前 6 个走上行、其余走下行。
   *
   * 资源来自 @lobehub/icons-static-svg（MIT）的 `-text` 锁定版（图标 + 字标），
   * 均为 fill="currentColor" 的官方单色版本——所以墙上不做灰度处理，
   * 直接用原色显示，避免踩各家 brand guideline 对改色的限制。
   * 换/加厂商见 public/logos/README.md。
   */
  vendors: [
    { name: "DeepSeek", slug: "deepseek", hasLogo: true },
    { name: "OpenAI", slug: "openai", hasLogo: true },
    { name: "Claude", slug: "claude", hasLogo: true },
    { name: "Gemini", slug: "gemini", hasLogo: true },
    { name: "Qwen", slug: "qwen", hasLogo: true },
    { name: "Kimi", slug: "kimi", hasLogo: true },
    { name: "智谱 GLM", slug: "zhipu", hasLogo: true },
    { name: "MiniMax", slug: "minimax", hasLogo: true },
    { name: "Grok", slug: "grok", hasLogo: true },
    { name: "MiMo", slug: "mimo", hasLogo: true },
    { name: "OpenRouter", slug: "openrouter", hasLogo: true },
  ] satisfies Vendor[],
  rowSplit: 6,

  /** 跑马灯下方的一句补充：说清「墙上没列的还能不能接」。 */
  note: {
    zh: "BYOK 自带密钥，也可走平台网关；常用厂商已内置预设，其余任意 OpenAI 兼容端点自定义接入。",
    en: "Bring your own key or use the platform gateway. Common vendors ship as presets; anything else plugs in via its OpenAI-compatible endpoint.",
  },
};

/*
 * 已核对过的模型 ID（来源：apps/server/agentcore/llm/pricing_data/community_prices.json
 * 与 apps/desktop/.../byokProviderPresets.ts）。
 * 首页 logo 墙按 amphora 的形态只放厂商标识，这些 ID 留给下载页 / 文档使用——
 * 别删，重新核一遍是有成本的。
 */
export const VERIFIED_MODEL_IDS = [
  "deepseek-v4-pro",
  "gpt-4o",
  "o3-mini",
  "claude-sonnet-4-5-20250929",
  "gemini-2.5-pro",
  "qwen-max",
  "kimi-k2.5",
  "glm-4-plus",
  "grok-3",
  "openrouter/auto",
];

/* ── 协作机制 ─────────────────────────────────────────────── */

export const MECHANISM = {
  eyebrow: { zh: "机制 · MECHANISM", en: "Mechanism" },
  title: {
    zh: "从一句话，到一支团队的产出",
    en: "From one sentence to a team's output",
  },
  headline: {
    zh: "就这么简单，只要[[四步]]",
    en: "It's this simple — [[in four steps]]",
  },
  lead: {
    zh: "你只出一句话。剩下的拆解、组队、编排、互审，交给 CEO 主 Agent。",
    en: "You supply one sentence. The lead agent handles the breakdown, the team, the scheduling and the review.",
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

/* ── 能做什么（四种交付物）─────────────────────────────────── */

/**
 * 交付物卡片。左右图文交替，accent 决定该行的主色与辉光。
 * accent 取招牌渐变的四个色停，让这一屏和站内其它渐变是同一套颜色。
 */
export const USECASES = {
  title: {
    zh: "一句话进去，成品出来",
    en: "One sentence in, finished work out",
  },
  lead: {
    zh: "App、网站、演示、长文——挑一个最贴近你手头那件事的。",
    en: "Apps, sites, decks, long-form — pick whichever is on your desk.",
  },
  cases: [
    {
      key: "app",
      accent: "1" as const,
      eyebrow: { zh: "应用", en: "APP" },
      title: { zh: "把一个想法做成能跑的应用。", en: "Turn an idea into a running app." },
      body: {
        zh: "从需求拆解到界面与接口，一支团队分工完成。",
        en: "From breakdown to UI and API — one team, split by role.",
      },
      bullets: [
        { zh: "前后端并行推进", en: "Front and back end in parallel" },
        { zh: "改完即可预览", en: "Preview the moment it changes" },
      ] satisfies T[],
      cta: { zh: "让团队做应用", en: "Build an app" },
    },
    {
      key: "site",
      accent: "2" as const,
      eyebrow: { zh: "网站", en: "WEBSITE" },
      title: { zh: "一句话说清，落地成整站。", en: "Say it once, get the whole site." },
      body: {
        zh: "文案、版式、响应式一起出，不是只给你一个模板。",
        en: "Copy, layout and responsiveness together — not just a template.",
      },
      bullets: [
        { zh: "结构与文案同时打磨", en: "Structure and copy tuned together" },
        { zh: "手机端一并适配", en: "Mobile handled in the same pass" },
      ] satisfies T[],
      cta: { zh: "让团队做网站", en: "Build a site" },
    },
    {
      key: "deck",
      accent: "3" as const,
      eyebrow: { zh: "演示", en: "DECK" },
      title: { zh: "把材料变成一份能讲的 PPT。", en: "Turn material into a deck you can present." },
      body: {
        zh: "先立叙事线，再填内容与图表，最后统一视觉。",
        en: "Narrative first, then content and charts, then one visual pass.",
      },
      bullets: [
        { zh: "逻辑线先行", en: "Story arc before slides" },
        { zh: "数据配图自动生成", en: "Charts generated from your data" },
      ] satisfies T[],
      cta: { zh: "让团队做演示", en: "Build a deck" },
    },
    {
      key: "paper",
      accent: "4" as const,
      eyebrow: { zh: "长文", en: "LONG-FORM" },
      title: { zh: "论文、调研、报告，成稿交付。", en: "Papers, research, reports — finished drafts." },
      body: {
        zh: "检索、分析、成文分角色推进，结论经过互审。",
        en: "Search, analysis and writing split by role; conclusions get reviewed.",
      },
      bullets: [
        { zh: "引用可追溯", en: "Citations you can trace" },
        { zh: "结论经质检打回重做", en: "Conclusions sent back until they hold" },
      ] satisfies T[],
      cta: { zh: "让团队写长文", en: "Write long-form" },
    },
  ],
};

/* ── 核心能力 ─────────────────────────────────────────────── */

export const CAPABILITIES = {
  eyebrow: { zh: "核心能力 · CAPABILITIES", en: "Capabilities" },
  title: {
    zh: "多 Agent 编排 · 全程可见 · 你是领导者",
    en: "Multi-agent orchestration · fully visible · you in charge",
  },
  headline: {
    zh: "你拿到的是一套\n经过验证的[[系统]]",
    en: "What you get is a proven [[system]]",
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
  headline: {
    zh: "从提示者，到[[领导者]]",
    en: "From prompter to [[lead]]",
  },
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
  headline: {
    zh: "架构不同，结果就[[不同]]",
    en: "Different architecture, different [[result]]",
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
        zh: "工具 / 技能 / 规则 / 记忆 / 团队，五类资产可沉淀复用",
        en: "Tools, skills, rules, memory, teams — five reusable asset types.",
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
  headline: {
    zh: "不止一个 Agent，\n而是一整套可[[沉淀]]的资产",
    en: "Not one agent —\na whole stack you can [[keep]]",
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
        zh: "团队行为的红线与约束",
        en: "The red lines and constraints your team follows.",
      },
      accent: "brand-2" as const,
    },
    {
      code: "Memory",
      name: { zh: "记忆", en: "Memory" },
      body: {
        zh: "跨会话沉淀的偏好、画像与项目上下文",
        en: "Preferences, profiles and project context that carry across sessions.",
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
    zh: "把下一件难事，交给一支团队。",
    en: "Hand the next hard thing to a team.",
  },
  headline: {
    zh: "把下一件难事，\n交给一支[[团队]]。",
    en: "Hand the next hard thing\nto a [[team]].",
  },
  lead: {
    zh: "AgentCore —— 让 AI 像团队一样工作。",
    en: "AgentCore — make AI work like a team.",
  },
  /** 收口信任信号（对齐旧站能力徽章，措辞须与真实能力一致）。 */
  trust: [
    { zh: "MCP", en: "MCP" },
    { zh: "可私有部署", en: "Self-host ready" },
    { zh: "网页 · 桌面 · 手机", en: "Web · Desktop · Mobile" },
  ] satisfies T[],
};

/* ── 404 ──────────────────────────────────────────────────── */

export const NOT_FOUND = {
  title: { zh: "这一页走丢了。", en: "This page went missing." },
  lead: {
    zh: "链接可能已经失效，或者地址敲错了一个字。下面几个出口应该能帮到你。",
    en: "The link may be dead, or a character slipped in the URL. These should get you back on track.",
  },
  home: { zh: "回首页", en: "Back to home" },
  app: { zh: "打开网页版", en: "Open the web app" },
  download: { zh: "下载客户端", en: "Desktop app" },
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
