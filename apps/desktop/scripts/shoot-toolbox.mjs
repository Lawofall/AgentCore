// Screenshot harness for 工具箱 (#/toolbox + the five 能力 sub-pages) — one PNG per
// page so AI can read back what the reworked sub-page navigation actually looks like.
//
// Usage:
//   node scripts/shoot-toolbox.mjs
//   node scripts/shoot-toolbox.mjs inbox            # substring filter on the page id
//   pnpm -C apps/desktop shoot:toolbox
//   SHOOT_THEME=dark pnpm -C apps/desktop shoot:toolbox
//
// Mechanism — same as scripts/shoot-settings.mjs, nothing new:
//   • webapp 壳 (vite.webapp.config.ts → index.webapp.html) with the REAL AuthGate,
//     satisfied by a stubbed `/v1/auth/me`. The offline preview entry (index.web.html)
//     sets `__WEB_PREVIEW__`, which makes AppShell skip its pollers — 自动化 would then
//     never fetch the standing-inbox badge, so the segment badge could not show up.
//   • Playwright `page.route` REST stubs with per-endpoint fixtures, so every page
//     renders POPULATED rather than empty/loading. No product code is touched.
//   • `VITE_API_URL` pinned to "" ⇒ same-origin API, no CORS on `route.fulfill`.
//
// One thing settings does not need: 连接器 talks to `window.mcpApi` (an Electron
// preload bridge), which a browser never has — the page would honestly degrade to
// 「本机 MCP 仅桌面端可用」. An `addInitScript` installs a stub bridge so the populated
// server list renders; that is browser-side test scaffolding, not a product change.
//
// Known gaps vs the real Electron app (screenshots differ, product is fine):
//   • Overlay scrollbars: headless Chromium's scrollbars take no width, so bugs where a
//     scrollbar gutter clips content cannot show up here (frontend-preview.mdc).
//     SHOOT_FIT (default on) grows the viewport to the full page height; set
//     SHOOT_FIT=0 for a fixed 1440x900 shot that at least keeps the page scrollable.
//   • Browser runtime (`__WEB__`) hides the desktop TitleBar and the DEV-only 实验 tile
//     group is present because Vite dev sets `import.meta.env.DEV`.
//
// Env knobs: SHOOT_THEME=dark · SHOOT_WIDTH · SHOOT_HEIGHT · SHOOT_SCALE ·
//            SHOOT_SETTLE_MS · SHOOT_FIT=0 · SHOOT_MAX_HEIGHT

import { mkdir, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

const here = dirname(fileURLToPath(import.meta.url));
const desktopDir = resolve(here, "..");
const SHOOT_OUT_DIR = "shoot-out-toolbox";
const outDir = resolve(desktopDir, SHOOT_OUT_DIR);

const SETTLE_MS = Number(process.env.SHOOT_SETTLE_MS ?? 900);
const VIEWPORT = {
  width: Number(process.env.SHOOT_WIDTH ?? 1440),
  height: Number(process.env.SHOOT_HEIGHT ?? 900),
};
const SCALE = Number(process.env.SHOOT_SCALE ?? 2);
const THEME = process.env.SHOOT_THEME === "dark" ? "dark" : "light";
// Grow the viewport to the page's full height so one PNG shows the whole page.
const FIT = process.env.SHOOT_FIT !== "0";
const MAX_HEIGHT = Number(process.env.SHOOT_MAX_HEIGHT ?? 4000);
const filter = (process.argv[2] ?? "").toLowerCase();

/** The capability segment bar, in `ToolboxPageHeader` order. */
const SEGMENTS = ["工具", "AI 提示词", "自动化", "工作流", "连接器"];

/**
 * 工具箱首页 + the five 能力 sub-pages (自动化 twice, once per inner tab).
 *
 * `segment` is the segment expected to be lit — the sub-pages carry no `<h1>`
 * (current position is the segment highlight), so it doubles as the audit's
 * expected value. `ready` is a text marker that only exists once the page's data
 * arrived, so we never shoot an empty or loading state. `heading` is the `<h1>`
 * the 工具箱 index still owns.
 */
const PAGES = [
  { id: "01-toolbox-home", hash: "/toolbox", heading: "工具箱", ready: "产品手册" },
  { id: "02-tools", hash: "/toolbox/tools", segment: "工具", ready: "调用参数" },
  {
    id: "03-guidelines",
    hash: "/toolbox/guidelines",
    segment: "AI 提示词",
    ready: "全员共享准则",
  },
  {
    id: "04-automations-tasks",
    hash: "/toolbox/automations",
    segment: "自动化",
    tab: "任务",
    ready: "立即触发",
  },
  {
    id: "05-automations-inbox",
    hash: "/toolbox/automations/inbox",
    segment: "自动化",
    tab: "收件箱",
    ready: "待拍板",
  },
  {
    id: "06-workflows",
    hash: "/toolbox/workflows",
    segment: "工作流",
    ready: "跑一次",
  },
  {
    id: "07-connectors",
    hash: "/toolbox/connectors",
    segment: "连接器",
    ready: "测试握手",
  },
];

// ---------------------------------------------------------------------------
// REST fixtures — shapes follow the OpenAPI DTOs in packages/contract-rest-types
// (CapabilitiesResponse / StandingTaskSummary / StandingTaskRunListResponse /
// FolderSummary / UserResponse …) and the hand-written wire types the workflow
// client declares (services/workflows.ts). Values are synthetic demo data,
// deliberately non-empty so every page shows its populated state.
// ---------------------------------------------------------------------------

const ISO = "2026-08-01T09:00:00.000Z";
const minutesAgo = (m) => new Date(Date.now() - m * 60_000).toISOString();
const minutesAhead = (m) => new Date(Date.now() + m * 60_000).toISOString();

const MOCK_USER = {
  id: "user_shoot",
  username: "dev",
  display_name: "自检账号",
  email: "dev@example.com",
  role: "user",
  created_at: ISO,
  password_must_change: false,
  avatar_url: null,
};

const obj = (properties, required = []) => ({
  type: "object",
  properties,
  required,
});

/** `CapabilityTool` — approval ∈ {never, grantable}, available_to ⊆ {ceo, worker}. */
const tool = (name, category, description, parameters, opts = {}) => ({
  name,
  category,
  description,
  parameters,
  approval: opts.approval ?? "never",
  available_to: opts.availableTo ?? ["ceo", "worker"],
});

const CAPABILITY_TOOLS = [
  tool(
    "web_search",
    "research",
    "联网检索：给出查询词，返回带出处的结果摘要。一次只搜 2–3 个核心词，其余概念下一轮再搜。",
    obj(
      {
        query: { type: "string", description: "检索词，建议 2–3 个核心词。" },
        max_results: { type: "integer", description: "结果数量上限，默认 8，最多 12。" },
      },
      ["query"],
    ),
  ),
  tool(
    "read_url",
    "research",
    "读取网页正文并转成可引用的文本；搜索结果不足以判断时用它把原文拉回来。",
    obj(
      {
        url: { type: "string", description: "要读取的网页 URL。" },
        max_chars: { type: "integer", description: "返回的最大字符数，默认 8000。" },
      },
      ["url"],
    ),
  ),
  tool(
    "search_conversations",
    "search",
    "按关键词检索历史对话，用于「上次那个方案」这类回溯；周期复盘任务应带 lookback_hours。",
    obj({
      query: { type: "string", description: "检索词；留空则按时间窗返回。" },
      lookback_hours: { type: "integer", description: "只看最近 N 小时（1–720）。" },
    }),
  ),
  tool(
    "read_conversation",
    "search",
    "读取指定对话的完整回合记录，拿到检索命中之后的上下文原文。",
    obj({ conversation_id: { type: "string", description: "对话 id。" } }, [
      "conversation_id",
    ]),
  ),
  tool(
    "file_read",
    "filesystem",
    "读取工作区内的文件内容，支持按行区间截取。",
    obj(
      {
        path: { type: "string", description: "工作区相对路径。" },
        offset: { type: "integer", description: "起始行（1 起）。" },
        limit: { type: "integer", description: "读取行数。" },
      },
      ["path"],
    ),
  ),
  tool(
    "file_list",
    "filesystem",
    "列出目录条目（名称、类型、大小），用于先看清目录再决定读哪一个。",
    obj({ path: { type: "string", description: "目录路径，默认工作区根。" } }),
  ),
  tool(
    "grep",
    "filesystem",
    "在工作区里按正则搜索内容，返回命中行；files_only 模式只返回文件名。",
    obj(
      {
        pattern: { type: "string", description: "正则表达式。" },
        path: { type: "string", description: "限定搜索目录。" },
        max_results: { type: "integer", description: "最大匹配行数，默认 50，最多 200。" },
      },
      ["pattern"],
    ),
  ),
  tool(
    "file_write",
    "filesystem",
    "写入文件（不存在则创建）。覆盖式写入，改局部请用 str_replace。",
    obj(
      {
        path: { type: "string", description: "工作区相对路径。" },
        content: { type: "string", description: "完整文件内容。" },
      },
      ["path", "content"],
    ),
    { approval: "grantable" },
  ),
  tool(
    "str_replace",
    "filesystem",
    "把文件里的一段文本替换成另一段；默认要求唯一匹配，避免改错位置。",
    obj(
      {
        path: { type: "string", description: "工作区相对路径。" },
        old_string: { type: "string", description: "被替换的原文（须唯一）。" },
        new_string: { type: "string", description: "替换后的文本。" },
      },
      ["path", "old_string", "new_string"],
    ),
    { approval: "grantable" },
  ),
  tool(
    "terminal",
    "execution",
    "在工作区终端执行命令，进程跨回合存活；仅本地模式可用。",
    obj(
      {
        command: { type: "string", description: "要执行的命令。" },
        timeout_seconds: { type: "integer", description: "超时秒数；超时杀进程并诚实返回。" },
      },
      ["command"],
    ),
  ),
  tool(
    "code_execute",
    "execution",
    "在沙箱里跑一段代码并返回 stdout/stderr，用于算数、转格式、快速验证。",
    obj(
      {
        code: { type: "string", description: "要执行的代码。" },
        language: { type: "string", description: "语言，默认 python。" },
      },
      ["code"],
    ),
  ),
  tool(
    "test_run",
    "execution",
    "跑项目测试并把失败用例结构化回传，交付前自检用。",
    obj({ target: { type: "string", description: "测试目标（文件 / 用例名）。" } }),
  ),
  tool(
    "delegate",
    "orchestration",
    "把任务拆给队员并行推进：一次给出全部子任务与交付契约，等他们的产出上卷后整合。",
    obj(
      {
        tasks: {
          type: "array",
          description: "子任务列表，每项含角色、任务说明与交付物形态。",
        },
      },
      ["tasks"],
    ),
    { availableTo: ["ceo"] },
  ),
  tool(
    "debate",
    "orchestration",
    "就一个有分歧的议题开一轮辩论：多名队员各持立场，交叉审查后给结论。",
    obj(
      {
        topic: { type: "string", description: "议题一句话。" },
        rounds: { type: "integer", description: "轮数，默认 2。" },
      },
      ["topic"],
    ),
    { availableTo: ["ceo"] },
  ),
  tool(
    "consult",
    "orchestration",
    "按需取回某条能力指引的完整正文（渐进披露：平时只挂一行触发说明）。",
    obj({ name: { type: "string", description: "指引名。" } }, ["name"]),
    { availableTo: ["ceo"] },
  ),
  tool(
    "replan",
    "orchestration",
    "推翻当前拆法重排剩余步骤，用于中途发现方向错了。",
    obj({ reason: { type: "string", description: "为什么要重排。" } }, ["reason"]),
    { availableTo: ["ceo"] },
  ),
  tool(
    "remember",
    "orchestration",
    "把值得长期记住的事实落盘为记忆，可选全局或仅当前文件夹生效。",
    obj(
      {
        content: { type: "string", description: "要记住的内容。" },
        scope: { type: "string", description: "global（默认）或 folder。" },
      },
      ["content"],
    ),
  ),
  tool(
    "escalate",
    "orchestration",
    "队员遇到超出授权或信息不足的岔路时上报给 CEO，由其决策后再继续。",
    obj({ question: { type: "string", description: "要上报的问题。" } }, [
      "question",
    ]),
    { availableTo: ["worker"] },
  ),
  tool(
    "ask_user",
    "interaction",
    "向用户发问（通用澄清）：可随时插入、可连续多问；blocking 决定是否挂起等待。",
    obj(
      {
        message: { type: "string", description: "要问的话。" },
        blocking: { type: "boolean", description: "是否挂起等待回答，默认 true。" },
      },
      ["message"],
    ),
    { availableTo: ["ceo"] },
  ),
  tool(
    "desktop_notify",
    "interaction",
    "给用户发一条桌面通知，用于长任务跑完后叫人回来看。",
    obj(
      {
        title: { type: "string", description: "通知标题（≤120 字）。" },
        body: { type: "string", description: "通知正文（可选，≤500 字）。" },
      },
      ["title"],
    ),
  ),
  tool(
    "host_info",
    "interaction",
    "读取用户本机基本信息（操作系统、架构、主机名）；结果为不可信本机报告。",
    obj({}),
    { approval: "grantable" },
  ),
];

const PACK_SKILLS = [
  {
    name: "contract_review",
    summary: "合同审查：按条款清单逐条比对，标出偏离与缺失。",
    body: "## 合同审查\n\n1. 先抽取双方主体、期限、金额、违约与终止条款。\n2. 逐条对照标准清单，标出偏离项与缺失项。\n3. 输出「风险等级 + 建议改法 + 原文引用」三列表格。\n",
  },
  {
    name: "compliance_checklist",
    summary: "合规自查：按行业清单逐项确认，缺证据的项不许打勾。",
    body: "## 合规自查\n\n- 每一项必须有可引用的证据来源，拿不到证据就标「未确认」。\n- 禁止用「一般来说」替代具体条款编号。\n",
  },
];

const THIN_SKILLS = [
  {
    name: "delegate_advanced",
    summary: "团队编排进阶：怎么拆任务、怎么写交付契约、什么时候该并行。",
    body: "## 团队编排进阶\n\n- 一次给全所有子任务，别挤牙膏式追加。\n- 每个子任务写清交付物形态（form）与必需章节。\n",
  },
  {
    name: "debate_and_review",
    summary: "辩论与交叉审查：什么议题值得开辩、怎么设阵营、怎么收口。",
    body: "## 辩论与交叉审查\n\n- 只有存在真实取舍时才开辩；事实问题直接查。\n- 收口必须给出「选了什么 + 放弃了什么 + 为什么」。\n",
  },
  {
    name: "ask_user_card",
    summary: "向用户发问：什么时候该问、怎么把选项做成卡片而不是长段落。",
    body: "## 向用户发问\n\n- 一次只问真正挡住推进的那个问题。\n- 有限选项用 card，多问题用普通 ask_user（最多 5 问）。\n",
  },
];

const CAPABILITIES = {
  tools: CAPABILITY_TOOLS,
  skills: [...THIN_SKILLS, ...PACK_SKILLS],
  packs: [
    {
      id: "pack_legal",
      name: "法务合规包",
      summary: "合同与合规场景的领域能力：审查清单、风险分级、证据引用规范。",
      skills: PACK_SKILLS,
    },
  ],
  guidelines: {
    shared_base:
      "# 全员共享准则\n\n## 身份\n\n你是 AgentCore 团队中的一员，与人类用户协作完成真实工作。\n\n## 表达\n\n- 先给结论，再给依据。\n- 不确定就说不确定，禁止编造出处。\n\n## 工具使用\n\n- 能用确定性工具拿到的事实，不要靠推测。\n- 写盘前先确认落点，破坏性操作一律先问。\n",
    ceo_addon:
      "# CEO 专属提示词\n\n## 路由核心\n\n- 单点事实自己查；需要多视角或并行推进时才组队。\n- 组队前把交付契约写清楚，别让队员猜。\n\n## 按需目录\n\n- 目录里只挂一行触发说明，要用时才 consult 取回完整指引。\n",
    ceo: "# CEO 完整提示词\n\n（全员共享准则 + CEO 专属提示词，由同一套 compose 逻辑拼装，与线上回合逐字一致。）\n",
  },
};

const FOLDERS = [
  {
    id: "folder_ops",
    name: "运营",
    mode: "cloud",
    local_root_id: null,
    local_subpath: null,
    rel_path: "运营",
    parent_rel_path: null,
    created_at: ISO,
    updated_at: ISO,
  },
  {
    id: "folder_research",
    name: "市场研究",
    mode: "cloud",
    local_root_id: null,
    local_subpath: null,
    rel_path: "市场研究",
    parent_rel_path: null,
    created_at: ISO,
    updated_at: ISO,
  },
];

const AXES = {
  file_write: "session",
  command: "auto",
  team_kickoff: "rules",
  host: "session",
};

const STANDING_TASKS = [
  {
    id: "task_brief",
    name: "每周一竞品简报",
    trigger_kind: "schedule",
    schedule_preset: "weekly_mon",
    cron: null,
    folder_id: "folder_research",
    goal: "汇总上周竞品动态与定价变化，输出一页简报并落盘到「市场研究」。",
    permission_axes: AXES,
    enabled: true,
    next_run_at: minutesAhead(60 * 26),
    conversation_id: null,
    last_run_at: minutesAgo(60 * 142),
    webhook_id: null,
    webhook_url: null,
    webhook_secret: null,
    template_key: null,
    template_config: null,
    workflow_id: "wf_research",
    workflow_name: "竞品调研五步",
    created_at: ISO,
    updated_at: ISO,
  },
  {
    id: "task_daily",
    name: "每日运营日报",
    trigger_kind: "schedule",
    schedule_preset: "daily",
    cron: null,
    folder_id: "folder_ops",
    goal: "拉取昨日核心指标，标出异常波动并给出下一步建议。",
    permission_axes: AXES,
    enabled: true,
    next_run_at: minutesAhead(60 * 9),
    conversation_id: null,
    last_run_at: minutesAgo(60 * 15),
    webhook_id: null,
    webhook_url: null,
    webhook_secret: null,
    template_key: null,
    template_config: null,
    workflow_id: null,
    workflow_name: null,
    created_at: ISO,
    updated_at: ISO,
  },
  {
    id: "task_webhook",
    name: "线上告警接入",
    trigger_kind: "webhook",
    schedule_preset: null,
    cron: null,
    folder_id: "folder_ops",
    goal: "收到告警后拉日志定位，给出影响面判断与临时缓解方案。",
    permission_axes: AXES,
    enabled: false,
    next_run_at: null,
    conversation_id: null,
    last_run_at: minutesAgo(60 * 72),
    webhook_id: "wh_alerts",
    webhook_url: "/v1/webhooks/wh_alerts",
    webhook_secret: null,
    template_key: null,
    template_config: null,
    workflow_id: null,
    workflow_name: null,
    created_at: ISO,
    updated_at: ISO,
  },
];

/** Catalog of system templates — not installed, so the 系统任务 card shows. */
const STANDING_TASK_TEMPLATES = [
  {
    key: "daily_conversation_review",
    title: "每日复盘",
    description: "每天自动复盘近期对话，确认后才落盘记忆与文档。",
    default_name: "每日复盘",
    default_cron: "0 1 * * *",
    installed_task_id: null,
    enabled: null,
  },
];

/** badge = unacked awaiting_user + unacked failed = 3 (kept consistent with items). */
const STANDING_TASK_RUNS = {
  badge: 3,
  items: [
    {
      id: "run_await_1",
      standing_task_id: "task_brief",
      task_name: "每周一竞品简报",
      status: "awaiting_user",
      conversation_id: "conv_brief",
      user_message_id: null,
      summary: "竞品 B 的定价页改版，是否把对比表一起更新？需要你拍板后继续。",
      error: null,
      acked_at: null,
      trigger_source: "schedule",
      created_at: minutesAgo(95),
      started_at: minutesAgo(95),
      finished_at: minutesAgo(88),
    },
    {
      id: "run_failed_1",
      standing_task_id: "task_webhook",
      task_name: "线上告警接入",
      status: "failed",
      conversation_id: "conv_alert",
      user_message_id: null,
      summary: null,
      error: "拉取日志超时：上游服务 30s 未响应（已重试 2 次）。",
      acked_at: null,
      trigger_source: "webhook",
      created_at: minutesAgo(210),
      started_at: minutesAgo(210),
      finished_at: minutesAgo(209),
    },
    {
      id: "run_await_2",
      standing_task_id: "task_daily",
      task_name: "每日运营日报",
      status: "awaiting_user",
      conversation_id: "conv_daily",
      user_message_id: null,
      summary: "日报里要写的三条异常已定位，写盘到「运营」需要你授权。",
      error: null,
      acked_at: null,
      trigger_source: "schedule",
      created_at: minutesAgo(400),
      started_at: minutesAgo(400),
      finished_at: minutesAgo(392),
    },
    {
      id: "run_ok_1",
      standing_task_id: "task_daily",
      task_name: "每日运营日报",
      status: "succeeded",
      conversation_id: "conv_daily_prev",
      user_message_id: null,
      summary:
        "昨日活跃 12.4k（+3.1%），付费转化 2.8%（持平）；异常：华东节点 P95 延迟涨到 820ms，已附排查建议。",
      error: null,
      acked_at: null,
      trigger_source: "schedule",
      created_at: minutesAgo(1_440),
      started_at: minutesAgo(1_440),
      finished_at: minutesAgo(1_432),
    },
    {
      id: "run_ok_2",
      standing_task_id: "task_brief",
      task_name: "每周一竞品简报",
      status: "succeeded",
      conversation_id: "conv_brief_prev",
      user_message_id: null,
      summary: "上周竞品动态 6 条，其中 2 条涉及定价；简报已落盘到「市场研究/简报」。",
      error: null,
      acked_at: minutesAgo(2_800),
      trigger_source: "manual",
      created_at: minutesAgo(2_880),
      started_at: minutesAgo(2_880),
      finished_at: minutesAgo(2_871),
    },
  ],
};

const step = (id, role, task) => ({ id, kind: "agent_step", role, task });
const gate = (id, label) => ({ id, kind: "human_gate", label });
const edge = (from, to) => ({ from, to });

const USER_WORKFLOWS = [
  {
    id: "wf_research",
    name: "竞品调研五步",
    description: "从检索到成稿的固定拆法，产出一份可落盘的对比报告。",
    definition: {
      nodes: [
        step("s1", "检索员", "围绕 {{topic}} 检索近三个月公开资料，按来源可信度排序。"),
        step("s2", "分析师", "把检索结果整理成功能 / 定价 / 目标客群三张对比表。"),
        gate("g1", "确认对比维度"),
        step("s3", "撰稿人", "按确认后的维度成稿，结论先行，逐条附出处。"),
      ],
      edges: [edge("s1", "s2"), edge("s2", "g1"), edge("g1", "s3")],
      slots: [
        { key: "topic", label: "调研主题", default: "国内 AI 笔记类产品" },
        { key: "region", label: "地区范围", default: "中国大陆" },
      ],
    },
    source: null,
    version: 4,
    created_at: ISO,
    updated_at: minutesAgo(60 * 30),
  },
  {
    id: "wf_release",
    name: "发版前检查",
    description: null,
    definition: {
      nodes: [
        step("s1", "测试员", "跑一遍回归用例，把失败项按影响面分级。"),
        step("s2", "文档员", "核对变更说明与实际改动是否一致。"),
        gate("g1", "人工确认可发"),
      ],
      edges: [edge("s1", "g1"), edge("s2", "g1")],
    },
    source: null,
    version: 2,
    created_at: ISO,
    updated_at: minutesAgo(60 * 5),
  },
  {
    id: "wf_weekly",
    name: "周会材料准备",
    description: "把散落在各处的进度汇成一页，会前十分钟就能过完。",
    definition: {
      nodes: [
        step("s1", "收集员", "拉取本周各条线的进展与阻塞。"),
        step("s2", "编辑", "压缩成一页：做完了什么、卡在哪、下周做什么。"),
      ],
      edges: [edge("s1", "s2")],
      slots: [{ key: "week", label: "周次", default: "本周" }],
    },
    source: null,
    version: 7,
    created_at: ISO,
    updated_at: minutesAgo(60 * 74),
  },
];

/** ids must match OfficialTemplateGuide's PICK_WHEN keys for the guide box to render. */
const WORKFLOW_TEMPLATES = [
  {
    id: "map_fanout",
    title: "并行摸底",
    summary: "几名队员同时从不同角度摸一遍议题，快速拿到全貌。",
    primary_slots: "topic",
    slots: [
      { key: "topic", label: "议题", required: true, hint: "想弄懂的那件事" },
    ],
  },
  {
    id: "cite_write_review",
    title: "深度研究报告",
    summary: "检索 → 交叉验证 → 成稿，产出一份带出处的长文报告。",
    primary_slots: "topic",
    slots: [
      { key: "topic", label: "研究主题", required: true, hint: null },
      { key: "audience", label: "读者", required: false, hint: "写给谁看" },
    ],
  },
  {
    id: "build_app",
    title: "从零搭应用",
    summary: "从零搭一个小应用：脚手架→模块→联调→冒烟。",
    primary_slots: "app",
    slots: [
      { key: "app", label: "应用", required: true, hint: "要搭建的应用 / SPA 简述" },
    ],
  },
];

/** `McpServerListItem[]` — installed into `window.mcpApi` (see addInitScript). */
const MCP_SERVERS = [
  {
    id: "mcp_filesystem",
    name: "Filesystem",
    enabled: true,
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-filesystem", "D:/工作区"],
    runtimeStatus: "ready",
  },
  {
    id: "mcp_github",
    name: "GitHub",
    enabled: true,
    command: "npx",
    args: ["-y", "@modelcontextprotocol/server-github"],
    runtimeStatus: "failed",
    runtimeError: "握手失败：GITHUB_TOKEN 未配置（进程退出码 1）",
  },
  {
    id: "mcp_sqlite",
    name: "SQLite",
    enabled: false,
    command: "uvx",
    args: ["mcp-server-sqlite", "--db-path", "D:/工作区/data.db"],
    runtimeStatus: "idle",
  },
];

/** Exact-path fixtures (query string stripped). */
const FIXTURES = new Map([
  ["/readyz", { status: "ready", database: true }],
  ["/version", { version: "0.9.0", git_sha: "1a2b3c4d5e6f7a8b", built_at: ISO }],
  ["/updates/policy", { enabled: true, min_desktop_version: null }],

  ["/v1/auth/me", MOCK_USER],

  // 工具 / AI 提示词 (both read the same catalog through useCapabilities).
  ["/v1/capabilities", CAPABILITIES],
  // 工具页 additionally probes the chat model so it can decide whether to hang the
  // tools-gate hint on delegate/debate — a platform model with tools keeps it off.
  [
    "/v1/users/me/llm-providers",
    {
      billing_mode: "platform",
      default_model_profile_id: "profile_default",
      platform_available: true,
      platform_model: "deepseek-v4-flash",
      providers: [],
    },
  ],
  [
    "/v1/users/me/models",
    {
      byok_configured: false,
      current: { id: "deepseek-v4-flash", origin: "platform", provider_id: null },
      models: [
        {
          id: "deepseek-v4-flash",
          display_name: "DeepSeek V4 Flash",
          origin: "platform",
          vendor: "deepseek",
          available: true,
          badge: "免费额度",
          capabilities: ["tools", "reasoning"],
          context_length: 131072,
          price: null,
          provider_id: null,
          provider_label: null,
        },
      ],
    },
  ],

  // 自动化 · 任务 / 收件箱 (+ the shell poller that feeds the segment badge).
  ["/v1/standing-tasks", STANDING_TASKS],
  ["/v1/standing-task-templates", STANDING_TASK_TEMPLATES],
  ["/v1/standing-task-runs", STANDING_TASK_RUNS],

  // 工作流.
  ["/v1/workflows", USER_WORKFLOWS],
  ["/v1/workflow-playbook-templates", WORKFLOW_TEMPLATES],

  // Workspaces back the task rows' 工作区 line and the sidebar tree.
  ["/v1/folders", FOLDERS],

  // Ambient shell chrome (sidebar / banners / badges) — quiet, empty states.
  ["/v1/notices/active", { banner: null, modal: null, inbox: [] }],
  ["/v1/conversations", { data: [], page: 1, page_size: 100, total: 0 }],
  ["/v1/conversations/grouped", { folders: [], ungrouped: [] }],
  ["/v1/workspaces", { data: [], total: 0 }],
  ["/v1/messages/chats", { data: [], total: 0 }],
  ["/v1/messages/friends", { data: [], total: 0 }],
  ["/v1/users/me/autonomy", { policy: "less_interrupt" }],
]);

/** Endpoints that speak SSE — answer with an immediately-closed stream so the
 *  shell's firehoses back off instead of hammering a JSON 200. */
const SSE_PATHS = new Set(["/v1/realtime", "/v1/fulfill"]);

async function fulfillApi(route) {
  const { pathname } = new URL(route.request().url());

  if (SSE_PATHS.has(pathname)) {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream; charset=utf-8",
      body: ": shoot-toolbox stub\n\n",
    });
    return;
  }

  const fixture = FIXTURES.get(pathname);
  if (fixture !== undefined) {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(fixture),
    });
    return;
  }

  // Unknown read: the list shape covers most collection routes and keeps
  // consumers on their empty state rather than an error banner.
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], items: [], total: 0 }),
  });
}

/** How long a page gets to finish its queries before we shoot it anyway. */
const LOAD_TIMEOUT_MS = 15_000;

/**
 * Wait until the page stopped loading, so we never shoot a spinner.
 *
 * The obvious `getByText("加载中…").waitFor({ state: "detached" })` rules nothing
 * out: a locator matching no element already counts as detached, so that wait
 * returns instantly both before the spinner mounts and between two spinners on a
 * page that loads in stages. Poll for a stable absence instead, after the page's
 * own `ready` content marker.
 *
 * 自动化 · 任务/收件箱 spin a bare `Loader2` with no text at all, which is exactly
 * why every page here carries a `ready` marker that only exists once data landed.
 */
async function waitForLoaded(page, spec) {
  let sawReady = true;
  if (spec.ready) {
    sawReady = await page
      .getByText(spec.ready)
      .first()
      .waitFor({ state: "visible", timeout: LOAD_TIMEOUT_MS })
      .then(() => true)
      .catch(() => false);
  }

  const spinner = page.getByText("加载中…");
  const deadline = Date.now() + LOAD_TIMEOUT_MS;
  let quiet = 0;
  while (quiet < 2) {
    const count = await spinner.count().catch(() => 0);
    quiet = count === 0 ? quiet + 1 : 0;
    if (quiet >= 2 || Date.now() >= deadline) break;
    await page.waitForTimeout(200);
  }
  return { sawReady, quiet: quiet >= 2 };
}

/**
 * Read back what the header actually rendered, so a broken segment bar fails the
 * run instead of quietly shipping a wrong-looking PNG. Scoped to `<main>` — the
 * sidebar has its own 工具箱 entry and would otherwise count as a stray back link.
 */
async function auditPage(page) {
  return page.evaluate(() => {
    const main = document.querySelector("main");
    if (!main) return null;
    const nav = main.querySelector('nav[aria-label="工具箱能力"]');
    const links = nav ? [...nav.querySelectorAll("a")] : [];
    const labelOf = (a) => (a.textContent ?? "").replace(/\d+\+?$/, "").trim();
    const badgeOf = (a) => {
      const chip = a?.querySelector("[aria-label]");
      return chip ? (chip.textContent ?? "").trim() : null;
    };
    const header = main.querySelector("header");
    const headerBorder = header
      ? Number.parseFloat(getComputedStyle(header).borderBottomWidth) || 0
      : 0;
    // 页头压成一行后，内容比页头宽 = 这一行被撑破了。
    const headerOverflow = header
      ? Math.max(header.scrollWidth - header.clientWidth, 0)
      : 0;
    const backs = [...main.querySelectorAll("a")].filter(
      (a) =>
        (a.getAttribute("href") ?? "").replace(/^#/, "") === "/toolbox" &&
        (a.textContent ?? "").includes("工具箱"),
    );
    // 返回链接与分段条的垂直中心距：并排为 ~0，回到「返回链接独占一行」则是行高级别。
    const midOf = (el) => {
      const r = el.getBoundingClientRect();
      return (r.top + r.bottom) / 2;
    };
    const backToNavGap =
      nav && backs.length === 1
        ? Math.round(Math.abs(midOf(backs[0]) - midOf(nav)))
        : null;
    const innerTabs = [
      ...(main.querySelector('nav[aria-label="自动化分区"]')?.querySelectorAll("a") ??
        []),
    ].map((a) => ({
      label: (a.textContent ?? "").replace(/\d+\+?$/, "").trim(),
      active: a.getAttribute("aria-current") === "page",
    }));

    return {
      hasSegmentNav: !!nav,
      segments: links.map(labelOf),
      active: links
        .filter((a) => a.getAttribute("aria-current") === "page")
        .map(labelOf),
      automationsBadge: badgeOf(links.find((a) => labelOf(a) === "自动化")),
      h1: [...main.querySelectorAll("h1")].map((h) => (h.textContent ?? "").trim()),
      backLinks: backs.length,
      backToNavGap,
      headerBorder,
      headerOverflow,
      innerTabs,
    };
  });
}

/** Turn the audit into human-readable complaints; empty array = clean. */
function auditProblems(audit, spec) {
  if (!audit) return ["audit failed: no <main>"];
  const out = [];
  if (spec.segment) {
    if (!audit.hasSegmentNav) out.push("缺少能力分段条");
    else if (audit.segments.join("|") !== SEGMENTS.join("|")) {
      out.push(`分段条项不对：${audit.segments.join(" / ")}`);
    }
    if (audit.active.join("|") !== spec.segment) {
      out.push(
        `高亮应为「${spec.segment}」，实际「${audit.active.join(" / ") || "无"}」`,
      );
    }
    if (audit.h1.length > 0) out.push(`子页不该有 h1：${audit.h1.join(" / ")}`);
    if (audit.backLinks !== 1) out.push(`返回链接应恰好 1 个，实际 ${audit.backLinks}`);
    // 页头只有一行：返回链接与分段条并排，动作插槽落在同一行右端。
    if (audit.backToNavGap === null || audit.backToNavGap > 4) {
      out.push(`返回链接没和分段条并排（中心差 ${audit.backToNavGap ?? "?"}px）`);
    }
    if (audit.headerOverflow > 0) {
      out.push(`页头这一行被撑破 ${audit.headerOverflow}px`);
    }
    // 自动化页内的下划线 tab 自带基线，页头再画一条就成了两条叠着的横线。
    if (spec.tab) {
      if (audit.headerBorder > 0) out.push("页头下边框与页内 tab 基线叠成两条横线");
    } else if (audit.headerBorder <= 0) {
      out.push("页头与内容之间没有分隔线");
    }
    if (!audit.automationsBadge) out.push("自动化分段没有徽章");
  }
  if (spec.tab) {
    const active = audit.innerTabs.filter((t) => t.active).map((t) => t.label);
    if (active.join("|") !== spec.tab) {
      out.push(`内层 tab 高亮应为「${spec.tab}」，实际「${active.join(" / ") || "无"}」`);
    }
  }
  return out;
}

/**
 * Overflow (in px) of the scroll container that owns the page, found by walking up
 * from the segment bar (sub-pages carry no `<h1>` to anchor on) or the index page's
 * `<h1>`; falls back to the document scroller. 0 when everything already fits.
 */
async function measureOverflow(page) {
  return page.evaluate(() => {
    const anchor =
      document.querySelector('nav[aria-label="工具箱能力"]') ??
      document.querySelector("main h1");
    let el = anchor?.parentElement ?? null;
    while (el && el !== document.body) {
      const overflowY = getComputedStyle(el).overflowY;
      if (
        (overflowY === "auto" || overflowY === "scroll") &&
        el.scrollHeight > el.clientHeight + 1
      ) {
        return el.scrollHeight - el.clientHeight;
      }
      el = el.parentElement;
    }
    const doc = document.scrollingElement ?? document.documentElement;
    return Math.max(doc.scrollHeight - doc.clientHeight, 0);
  });
}

/**
 * Grow the viewport until the page stops overflowing, so one PNG holds the whole
 * thing. Iterative rather than measure-once: growing the viewport reflows content
 * (wider rows wrap shorter), and a late query can turn a page that measured as
 * fitting into one that overflows — so a zero reading only ends the loop when the
 * next one confirms it.
 */
async function fitViewport(page) {
  let settled = 0;
  for (let pass = 0; pass < 6 && settled < 2; pass += 1) {
    const overflow = await measureOverflow(page);
    if (overflow <= 0) {
      settled += 1;
      await page.waitForTimeout(250);
      continue;
    }
    settled = 0;
    const current = page.viewportSize()?.height ?? VIEWPORT.height;
    const height = Math.min(current + overflow + 24, MAX_HEIGHT);
    if (height <= current) break;
    await page.setViewportSize({ width: VIEWPORT.width, height });
    await page.waitForTimeout(250);
  }
}

async function main() {
  process.chdir(desktopDir);

  let pages = PAGES;
  if (filter) {
    pages = pages.filter(
      (p) =>
        p.id.toLowerCase().includes(filter) || p.hash.toLowerCase().includes(filter),
    );
  }
  if (pages.length === 0) {
    console.error(`No toolbox pages matched filter "${filter}".`);
    process.exitCode = 1;
    return;
  }

  // A filtered run refreshes just the pages it shot; only a full run starts clean.
  if (!filter) await rm(outDir, { recursive: true, force: true });
  await mkdir(outDir, { recursive: true });

  console.log("Booting webapp shell (vite.webapp.config.ts, same-origin API)…");
  const server = await createServer({
    configFile: resolve(desktopDir, "vite.webapp.config.ts"),
    logLevel: "warn",
    // Same-origin API (no CORS on stubbed responses) + no dev auto-login racing
    // the stubbed /v1/auth/me. Mirrors e2e/vite.e2e.config.ts's `define` pin.
    define: {
      "import.meta.env.VITE_API_URL": '""',
      "import.meta.env.VITE_DEV_USERNAME": '""',
      "import.meta.env.VITE_DEV_PASSWORD": '""',
    },
  });
  await server.listen();
  const base = server.resolvedUrls?.local?.[0];
  if (!base) {
    await server.close();
    throw new Error("Vite did not report a local URL.");
  }

  let browser;
  try {
    browser = await chromium.launch();
  } catch (err) {
    await server.close();
    console.error(
      `Failed to launch Chromium. Install once:\n  pnpm -C apps/desktop exec playwright install chromium\n${String(err?.message ?? err)}`,
    );
    process.exitCode = 1;
    return;
  }

  const page = await browser.newPage({
    viewport: VIEWPORT,
    deviceScaleFactor: SCALE,
    colorScheme: THEME,
  });
  await page.addInitScript((theme) => {
    try {
      // uiStorage namespace + JSON value (stores/ui.ts loadTheme).
      localStorage.setItem("agentcore:theme", JSON.stringify(theme));
    } catch {
      /* ignore */
    }
  }, THEME);

  // 连接器 reads the Electron preload bridge; a browser has none, so the page would
  // honestly degrade to「本机 MCP 仅桌面端可用」. Install a read-only stub bridge.
  await page.addInitScript((servers) => {
    const list = async () => ({ ok: true, servers });
    window.mcpApi = {
      listServers: list,
      upsertServer: async () => ({ ok: true, server: servers[0] }),
      removeServer: list,
      setServerEnabled: async () => ({ ok: true, server: servers[0] }),
      testServer: async () => ({ ok: true, status: "ready", tools: [] }),
      runOp: async () => ({ ok: false, error: { kind: "stub", detail: "shoot stub" } }),
    };
  }, MCP_SERVERS);

  await page.route("**/v1/**", fulfillApi);
  await page.route("**/readyz", fulfillApi);
  await page.route("**/version", fulfillApi);
  await page.route("**/updates/policy", fulfillApi);

  const pageErrors = [];
  page.on("pageerror", (err) => pageErrors.push(err.message));

  // Warm-up pass (not captured): the first navigation of a cold Vite dev server
  // spends seconds transforming the module graph, which is long enough that the
  // first page gets shot while its queries are still loading.
  try {
    const warm = new URL("index.webapp.html", base);
    warm.hash = PAGES[0].hash;
    await page.goto(warm.href, { waitUntil: "load", timeout: 60_000 });
    await page.locator("main h1").first().waitFor({ timeout: 30_000 });
    await page.waitForTimeout(SETTLE_MS);
  } catch {
    /* best-effort warm-up — the per-page loop reports real failures */
  }

  let ok = 0;
  const failures = [];

  for (const [i, spec] of pages.entries()) {
    const file = `${spec.id}${THEME === "dark" ? "-dark" : ""}.png`;
    const label = `[${i + 1}/${pages.length}] ${file}`;

    pageErrors.length = 0;
    let failure = null;
    const notes = [];
    await page.setViewportSize(VIEWPORT).catch(() => {});
    try {
      const url = new URL("index.webapp.html", base);
      url.searchParams.set("shoot-toolbox", spec.id);
      url.hash = spec.hash;
      await page.goto(url.href, { waitUntil: "load", timeout: 30_000 });

      // AuthGate resolves (stubbed /v1/auth/me) → AppShell → the page. The index
      // still owns an <h1>; the 能力 sub-pages are identified by the segment bar.
      if (spec.heading) {
        await page
          .locator("main h1", { hasText: spec.heading })
          .first()
          .waitFor({ state: "visible", timeout: 20_000 });
      } else {
        await page
          .locator('nav[aria-label="工具箱能力"]')
          .first()
          .waitFor({ state: "visible", timeout: 20_000 });
        // The badge rides a shell-level poller, not the page's own query, so wait
        // for it explicitly — otherwise a fast page gets shot badge-less.
        await page
          .locator('nav[aria-label="工具箱能力"] [aria-label$="条待处理"]')
          .first()
          .waitFor({ state: "visible", timeout: 10_000 })
          .catch(() => notes.push("徽章未出现"));
      }

      const loaded = await waitForLoaded(page, spec);
      if (!loaded.sawReady) notes.push(`没等到内容标记「${spec.ready}」`);
      if (!loaded.quiet) notes.push("仍有「加载中…」，图里可能是加载态");

      await page.evaluate(() => document.fonts?.ready).catch(() => {});
      await page.waitForTimeout(SETTLE_MS);

      if (FIT) await fitViewport(page);

      notes.push(...auditProblems(await auditPage(page), spec));
    } catch (err) {
      failure = String(err?.message ?? err);
    }

    await page.screenshot({ path: resolve(outDir, file) }).catch(() => {});
    if (pageErrors.length) {
      failure = `${failure ? `${failure}; ` : ""}page error: ${pageErrors.join(" | ")}`;
    }
    if (!failure && notes.length) failure = notes.join("；");

    if (failure) {
      failures.push({ name: file, error: failure });
      console.error(`  ✗ ${label} — ${failure}`);
    } else {
      ok += 1;
      const h = page.viewportSize()?.height ?? VIEWPORT.height;
      console.log(`  ✓ ${label} (${VIEWPORT.width}x${h})`);
    }
  }

  await browser.close();
  await server.close();

  console.log(`\nDone: ${ok}/${pages.length} → ${outDir}`);
  if (failures.length) {
    console.error(`${failures.length} failed:`);
    for (const f of failures) console.error(`  - ${f.name}: ${f.error}`);
    process.exitCode = 1;
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
