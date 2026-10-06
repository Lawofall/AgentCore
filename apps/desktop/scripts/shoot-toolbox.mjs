// Screenshot harness for 工具箱（官方 / 我的 / 市场三栏）.
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
//     sets `__WEB_PREVIEW__`, which makes AppShell skip its pollers.
//   • Playwright `page.route` REST stubs with per-endpoint fixtures, so every page
//     renders POPULATED rather than empty/loading. No product code is touched.
//   • `VITE_API_URL` pinned to "" ⇒ same-origin API, no CORS on `route.fulfill`.
//
// One thing settings does not need: 连接器 talk to `window.mcpApi`
// (an Electron preload bridge), which a browser never has — the 连接器 group
// would honestly stay hidden. An `addInitScript` installs a stub bridge so the
// populated server list renders on 提示词; that is browser-side test scaffolding,
// not a product change.
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

/**
 * 三栏页：可见 PageHeader「工具箱」+ 来源 tab。默认落地「我的」。
 * `ready` waits until populated fixtures landed.
 */
const PAGES = [
  {
    id: "01-toolbox-home",
    hash: "/toolbox",
    heading: "工具箱",
    ready: "必带",
    expectMarketChrome: true,
  },
  {
    id: "02-tools",
    hash: "/toolbox/official",
    heading: "工具箱",
    ready: "联网检索",
    click: "联网检索",
    afterClick: "要填",
    expectMarketChrome: true,
  },
  {
    id: "03-guidelines",
    hash: "/toolbox/official",
    heading: "工具箱",
    ready: "页面观感",
    click: "页面观感",
    afterClick: "动手前",
    expectMarketChrome: true,
  },
  {
    id: "04-store",
    hash: "/toolbox/market",
    heading: "工具箱",
    ready: "审合同时用",
    click: "合同审查",
    afterClick: "先列争议条款",
    expectMarketChrome: true,
  },
  {
    id: "08-connectors",
    hash: "/toolbox/mine/skills?connectors=1",
    heading: "工具箱",
    ready: "新建连接器",
    expectMarketChrome: true,
  },
  {
    id: "11-market-empty",
    hash: "/toolbox/market",
    heading: "工具箱",
    ready: "还没有可安装的内容",
    emptyPaths: ["/v1/skill-store"],
    expectMarketChrome: true,
  },
];

// ---------------------------------------------------------------------------
// REST fixtures — shapes follow the OpenAPI DTOs in packages/contract-rest-types
// (CapabilitiesResponse / FolderSummary / UserResponse …). Values are synthetic demo data,
// deliberately non-empty so every page shows its populated state.
// ---------------------------------------------------------------------------

const ISO = "2026-08-01T09:00:00.000Z";

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

const FACE_FROM_CATEGORY = {
  research: "web",
  search: "search",
  filesystem: "file",
  execution: "execution",
  orchestration: "orchestration",
  interaction: "orchestration",
};

/** `CapabilityTool` — approval ∈ {never, grantable}, available_to ⊆ {ceo, worker}. */
const tool = (name, category, description, parameters, opts = {}) => ({
  name,
  face: opts.face ?? FACE_FROM_CATEGORY[category] ?? "web",
  resident: opts.resident ?? false,
  summary: opts.summary ?? description.split(/[：。]/)[0],
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
      },
      ["query"],
    ),
  ),
  tool(
    "web_fetch",
    "research",
    "读取网页正文并转成可引用的文本；搜索结果不足以判断时用它把原文拉回来。",
    obj(
      {
        url: { type: "string", description: "要读取的网页 URL。" },
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
    "read",
    "filesystem",
    "读取工作区内的文件内容，支持按行区间截取。",
    obj(
      {
        file_path: { type: "string", description: "工作区相对路径。" },
        offset: { type: "integer", description: "起始行（1 起）。" },
        limit: { type: "integer", description: "读取行数。" },
      },
      ["file_path"],
    ),
    { resident: true },
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
      },
      ["pattern"],
    ),
  ),
  tool(
    "write",
    "filesystem",
    "写入文件（不存在则创建）。覆盖式写入，改局部请用 edit。",
    obj(
      {
        file_path: { type: "string", description: "工作区相对路径。" },
        content: { type: "string", description: "完整文件内容。" },
      },
      ["file_path", "content"],
    ),
    { approval: "grantable" },
  ),
  tool(
    "edit",
    "filesystem",
    "把文件里的一段文本替换成另一段；默认要求唯一匹配，避免改错位置。",
    obj(
      {
        file_path: { type: "string", description: "工作区相对路径。" },
        old_string: { type: "string", description: "被替换的原文（须唯一）。" },
        new_string: { type: "string", description: "替换后的文本。" },
      },
      ["file_path", "old_string", "new_string"],
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
    "不主动启动仅推荐：结构化正反辩论。",
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
    { availableTo: ["ceo"], resident: true },
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
    { resident: true },
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
    "host_info",
    "interaction",
    "读取用户本机基本信息（操作系统、架构、主机名）；结果为不可信本机报告。",
    obj({}),
    { approval: "grantable", face: "host_browser" },
  ),
];

const THIN_SKILLS = [
  {
    name: "delegate_advanced",
    summary: "团队编排进阶：怎么拆任务、怎么写交付契约、什么时候该并行。",
    body: "## 团队编排进阶\n\n- 一次给全所有子任务，别挤牙膏式追加。\n- 每个子任务写清交付物形态（form）与必需章节。\n",
  },
  {
    name: "page_ui",
    summary: "页面观感",
    body: "## 页面观感\n\n- 动手前先用一句人能听懂的方向定调。\n- 从零展示页要有辨识度，不等于套通用模板脸。\n",
  },
  {
    name: "ask_user_card",
    summary: "向用户发问：什么时候该问、怎么把选项做成卡片而不是长段落。",
    body: "## 向用户发问\n\n- 一次只问真正挡住推进的那个问题。\n- 有限选项用 card，多问题用普通 ask_user（最多 5 问）。\n",
  },
];

const CAPABILITIES = {
  tools: CAPABILITY_TOOLS,
  skills: THIN_SKILLS,
  guidelines: {
    shared_base:
      "# 全员共享准则\n\n## 身份\n\n你是 AgentCore 团队中的一员，与人类用户协作完成真实工作。\n\n## 表达\n\n- 先给结论，再给依据。\n- 不确定就说不确定，禁止编造出处。\n\n## 工具使用\n\n- 能用确定性工具拿到的事实，不要靠推测。\n- 写盘前先确认落点，破坏性操作一律先问。\n",
    worker_leaf:
      "<身份>\n你是 AgentCore 的队员，只负责划定好的这一件任务（所需上下文已给你）。不能再向下委派。够不到用户。\n</身份>\n\n【落盘文件】（form=files）成品写入工作区；正文只报路径、怎么用、关键取舍。\n\n【纯文字】（form=prose）成品就是正文。不要落盘。\n\n【改工程】（form=workspace）就地改用户工程，不要写入 `AgentCore/文档/`。正文只报路径、怎么跑、关键取舍。\n",
    worker_captain:
      "<身份>\n你是 AgentCore 的队员，只负责划定好的这一件任务（所需上下文已给你）。够不到用户。你的子成员仍可再向下委派一层。\n</身份>\n\n【落盘文件】（form=files）成品写入工作区；正文只报路径、怎么用、关键取舍。\n\n【纯文字】（form=prose）成品就是正文。不要落盘。\n\n【改工程】（form=workspace）就地改用户工程，不要写入 `AgentCore/文档/`。正文只报路径、怎么跑、关键取舍。\n",
    ceo_addon:
      "<身份>\n你是 AgentCore 的 CEO：用户是老板，只跟你说话；你带队执行，对整段对话负责到底。\n</身份>\n\n<按需目录>\n- staffing：团队拆法\n- lead_subteam：子队拆法\n</按需目录>\n",
    ceo: "# CEO 完整提示词\n\n（全员共享准则，由同一套 compose 逻辑拼装，与线上回合逐字一致。）\n",
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
  [
    "/v1/skill-catalog",
    {
      slots: [
        ...THIN_SKILLS.map((skill) => ({
          name: skill.name,
          summary: skill.summary,
        })),
      ],
      mine: [
        {
          id: "mine_1",
          name: "提问卡",
          description: "问用户时用这份",
          content:
            "---\napply: on_demand\ndescription: 问用户时用这份\n---\n一次只问挡住推进的那件事。\n",
          version: "v1",
        },
      ],
      folder_id: null,
      writable: true,
    },
  ],
  // 提示词 additionally probes the chat model so it can decide whether to hang the
  // tools-gate hint on delegate/debate — a platform model with tools keeps it off.
  [
    "/v1/users/me/llm-providers",
    {
      billing_mode: "platform",
      default_assembly_id: "profile_default",
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

  // 能力商店（列表无正文；详情另见 pathname /v1/skill-store/:id）.
  [
    "/v1/skill-store",
    {
      data: [
        {
          id: "listing_contract",
          name: "合同审查",
          description: "审合同时用",
          author: "甲",
          version_n: 1,
          installed: false,
          has_update: false,
          status: "published",
          source_document_id: "doc_contract",
        },
        {
          id: "listing_brief",
          name: "竞品简报",
          description: "每周出一份对照表",
          author: "乙",
          version_n: 2,
          installed: true,
          has_update: true,
          status: "published",
          source_document_id: "doc_brief",
        },
        {
          id: "listing_weekly",
          name: "周报助手",
          description: "把本周材料收成一页",
          author: "丙",
          version_n: 1,
          installed: false,
          has_update: false,
          status: "published",
          source_document_id: "doc_weekly",
        },
        {
          id: "listing_minutes",
          name: "会议纪要",
          description: "录音或笔记整理成待办",
          author: "丁",
          version_n: 1,
          installed: false,
          has_update: false,
          status: "published",
          source_document_id: "doc_minutes",
        },
        {
          id: "listing_claim",
          name: "报销核对",
          description: "对照发票查缺漏项",
          author: "戊",
          version_n: 3,
          installed: false,
          has_update: false,
          status: "published",
          source_document_id: "doc_claim",
        },
        {
          id: "listing_onboard",
          name: "入职清单",
          description: "新同事第一周要办的事",
          author: "己",
          version_n: 1,
          installed: false,
          has_update: false,
          status: "published",
          source_document_id: "doc_onboard",
        },
      ],
      page: 1,
      page_size: 24,
      total: 6,
    },
  ],
  [
    "/v1/skill-store/mine",
    {
      data: [
        {
          id: "listing_ask",
          name: "提问卡",
          description: "问用户时用这份",
          author: "我",
          version_n: 1,
          installed: false,
          has_update: false,
          status: "published",
          source_document_id: "mine_1",
        },
      ],
    },
  ],
  [
    "/v1/skill-store/listing_contract",
    {
      id: "listing_contract",
      name: "合同审查",
      description: "审合同时用",
      author: "甲",
      version_n: 1,
      installed: false,
      has_update: false,
      status: "published",
      source_document_id: "doc_contract",
      content: "先列争议条款，再对照模板改。\n",
    },
  ],

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
/** Per-page override so empty-state shots do not reuse the populated fixtures. */
let emptyPaths = new Set();

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

  if (emptyPaths.has(pathname)) {
    const fixture = FIXTURES.get(pathname);
    const body = Array.isArray(fixture)
      ? []
      : { data: [], items: [], total: 0, page: 1, page_size: 24 };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
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
 * own `ready` content marker that only exists once data landed.
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
 * Read back what the header actually rendered, so a leftover segment bar or a
 * missing page title fails the run instead of quietly shipping a wrong-looking PNG.
 * Scoped to `<main>` — the sidebar has its own 工具箱 entry and would otherwise
 * count as a stray back link.
 */
async function auditPage(page) {
  return page.evaluate(() => {
    const main = document.querySelector("main");
    if (!main) return null;
    const hrefPath = (a) => (a.getAttribute("href") ?? "").replace(/^#/, "");
    const nav = main.querySelector('nav[aria-label="工具箱能力"]');
    const kindNav = main.querySelector('nav[aria-label="工具箱种类"]');
    const marketChips = main.querySelector('[aria-label="货架种类"]');
    const autoTabs = main.querySelector('nav[aria-label="自动化分区"]');
    const hasPageHeader = [...main.querySelectorAll("header h1")].some(
      (h) => !h.classList.contains("sr-only"),
    );
    const chrome = main.querySelector("header");
    const chromeOverflow = chrome
      ? Math.max(chrome.scrollWidth - chrome.clientWidth, 0)
      : 0;
    const backs = [...main.querySelectorAll("a")].filter((a) => {
      const path = hrefPath(a);
      return (
        (a.textContent ?? "").includes("工具箱") &&
        (path === "/toolbox" || path === "/toolbox/mine/skills")
      );
    });
    const hasMarketChrome = [...main.querySelectorAll("a")].some(
      (a) =>
        hrefPath(a) === "/toolbox/market" &&
        (a.textContent ?? "").includes("市场"),
    );
    const hasGuidesChrome = [...main.querySelectorAll("a")].some(
      (a) =>
        hrefPath(a) === "/toolbox/guides" &&
        (a.textContent ?? "").includes("说明书"),
    );
    const hasManualLink = [...main.querySelectorAll("a")].some((a) =>
      (a.textContent ?? "").includes("手册"),
    );

    return {
      hasSegmentNav: !!nav,
      hasKindNav: !!kindNav,
      hasMarketChips: !!marketChips,
      hasAutoTabs: !!autoTabs,
      h1: [...main.querySelectorAll("h1")].map((h) =>
        (h.textContent ?? "").trim(),
      ),
      backLinks: backs.length,
      hasPageHeader,
      chromeOverflow,
      hasMarketChrome,
      hasGuidesChrome,
      hasManualLink,
    };
  });
}

/** Turn the audit into human-readable complaints; empty array = clean. */
function auditProblems(audit, spec) {
  if (!audit) return ["audit failed: no <main>"];
  const out = [];
  if (spec.heading && !audit.h1.includes(spec.heading)) {
    out.push(
      `标题应为「${spec.heading}」，实际「${audit.h1.join(" / ") || "无"}」`,
    );
  }
  if (audit.hasSegmentNav) out.push("不该再有能力分段条");
  if (audit.hasAutoTabs) out.push("不该再有自动化分区 tab");
  if (audit.hasKindNav) out.push("不该再有种类 tab");
  if (audit.hasManualLink) out.push("顶栏不应再有手册");
  if (audit.chromeOverflow > 0) {
    out.push(`顶栏这一行被撑破 ${audit.chromeOverflow}px`);
  }
  if (spec.deep) {
    if (!audit.hasPageHeader) out.push("深页应有 PageHeader");
    if (audit.backLinks < 1) out.push("深页应有返回工具箱");
    if (audit.hasMarketChrome) out.push("深页不应再挂市场入口");
    if (audit.hasGuidesChrome) out.push("深页不应再挂说明书入口");
  } else {
    if (!audit.hasPageHeader) out.push("目录页应有可见 PageHeader");
    if (audit.backLinks !== 0) {
      out.push(`壳内不应有返回工具箱链接，实际 ${audit.backLinks}`);
    }
    if (spec.expectMarketChrome && !audit.hasMarketChrome) {
      out.push("顶栏应有市场");
    }
    if (audit.hasGuidesChrome) out.push("目录页不应再挂说明书");
  }
  if (spec.hash === "/toolbox/market" && audit.hasMarketChips) {
    out.push("市场页不应再有货架种类 chip");
  }
  return out;
}

/**
 * Overflow (in px) of the scroll container that owns the page, found by walking up
 * from the page `<h1>`; falls back to the document scroller. 0 when everything already fits.
 */
async function measureOverflow(page) {
  return page.evaluate(() => {
    const anchor = document.querySelector("main h1");
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

  // 连接器 reads the Electron preload bridge; a browser has none, so the group
  // would stay hidden. Install a read-only stub bridge.
  await page.addInitScript((servers) => {
    const list = async () => ({ ok: true, servers });
    window.mcpApi = {
      listServers: list,
      upsertServer: async () => ({ ok: true, server: servers[0] }),
      removeServer: list,
      setServerEnabled: async () => ({ ok: true, server: servers[0] }),
      testServer: async () => ({ ok: true, status: "ready", tools: [] }),
      runOp: async (input) => {
        if (input?.op === "list_tools") {
          return {
            ok: true,
            value: {
              servers: [
                {
                  id: "mcp_filesystem",
                  name: "Filesystem",
                  status: "ready",
                  tools: [
                    {
                      name: "read_file",
                      description: "Read a file from the workspace.",
                      inputSchema: {
                        type: "object",
                        properties: {
                          path: {
                            type: "string",
                            description: "Absolute path.",
                          },
                        },
                        required: ["path"],
                      },
                    },
                    {
                      name: "write_file",
                      description: "Write a file to the workspace.",
                      inputSchema: {
                        type: "object",
                        properties: {
                          path: { type: "string" },
                          content: { type: "string" },
                        },
                      },
                    },
                  ],
                },
                {
                  id: "mcp_github",
                  name: "GitHub",
                  status: "failed",
                  error: "握手失败：GITHUB_TOKEN 未配置（进程退出码 1）",
                  tools: [],
                },
              ],
            },
          };
        }
        return { ok: false, error: { kind: "stub", detail: "shoot stub" } };
      },
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
    await page
      .locator("main h1", { hasText: PAGES[0].heading })
      .first()
      .waitFor({ state: "attached", timeout: 30_000 });
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
    emptyPaths = new Set(spec.emptyPaths ?? []);
    await page.setViewportSize(VIEWPORT).catch(() => {});
    try {
      const url = new URL("index.webapp.html", base);
      url.searchParams.set("shoot-toolbox", spec.id);
      url.hash = spec.hash;
      await page.goto(url.href, { waitUntil: "load", timeout: 30_000 });

      // AuthGate resolves (stubbed /v1/auth/me) → AppShell → the page.
      if (spec.heading) {
        await page
          .locator("main h1", { hasText: spec.heading })
          .first()
          .waitFor({ state: "attached", timeout: 20_000 });
      }
      const loaded = await waitForLoaded(page, spec);
      if (!loaded.sawReady) notes.push(`没等到内容标记「${spec.ready}」`);
      if (!loaded.quiet) notes.push("仍有「加载中…」，图里可能是加载态");

      if (spec.overlayReady) {
        await page
          .getByText(spec.overlayReady)
          .first()
          .waitFor({ state: "visible", timeout: 10_000 })
          .catch(() => notes.push(`没等到 overlay「${spec.overlayReady}」`));
      }
      if (spec.click) {
        const scope = spec.clickWithin
          ? page.getByTestId(spec.clickWithin)
          : page;
        await scope.getByRole("button", { name: spec.click }).click();
        if (spec.afterClick) {
          await page
            .getByText(spec.afterClick)
            .first()
            .waitFor({ state: "visible", timeout: 10_000 })
            .catch(() => notes.push(`点「${spec.click}」后没看到「${spec.afterClick}」`));
        }
      }

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
