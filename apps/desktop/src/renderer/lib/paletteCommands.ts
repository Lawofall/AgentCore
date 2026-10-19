import { hasLocalFiles, hasWindowControls } from "@/lib/capabilities";
import { setComposerChannelPreference } from "@/lib/composerChannelPreference";
import {
  CURRENT_SCHEME_ID,
  getSchemeId,
  listSchemeGroups,
  listSchemes,
  setSchemeId,
  setSchemePanelOpen,
} from "@/lib/devSchemes";
import { isNarrowHiddenPaletteId } from "@/lib/narrowProduct";
import { startNewConversation } from "@/lib/newConversation";
import { pickAndOpenLocalFolder } from "@/lib/openLocalFolder";
import { chord } from "@/lib/shortcuts";
import { notifyError } from "@/lib/toast";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { exportConversation } from "@/services/conversations";
import {
  type DemoTapeSummary,
  prepareDemoTapeAndOpen,
  startDemoTapeAndOpen,
} from "@/services/demoTape";
import { openCurrentConversationTerminal } from "@/services/terminalActions";
import { useConversationStore } from "@/stores/conversation";
import { useFoldersStore } from "@/stores/folders";
import { useShareStore } from "@/stores/share";
import { useSidebarStore } from "@/stores/sidebar";
import { useUIStore } from "@/stores/ui";
import {
  WINDOW_FRAME_PRESETS,
  type WindowFramePreset,
} from "@shared/window-contract";
import {
  BarChart3,
  BookOpen,
  Clapperboard,
  CloudUpload,
  Cpu,
  CreditCard,
  Download,
  Files,
  FlaskConical,
  FolderKey,
  GitBranch,
  Globe,
  HardDrive,
  Info,
  KeyRound,
  Keyboard,
  type LucideIcon,
  Mail,
  MessagesSquare,
  Monitor,
  Moon,
  PanelLeft,
  Plus,
  RotateCcw,
  Scan,
  ScrollText,
  Settings,
  Share2,
  SlidersHorizontal,
  Store,
  Sun,
  Terminal,
  Unplug,
  UserCog,
  Workflow,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import type { NavigateFunction } from "react-router-dom";

/** Command palette sub-sections (Tier 2). Rendered in this fixed order, each as
 * its own group so actions stay scannable next to the entity search results. */
export type CommandCategory = "操作" | "前往" | "主题" | "开发";

export const COMMAND_CATEGORY_ORDER: CommandCategory[] = [
  "操作",
  "前往",
  "主题",
];

export function paletteCategoryOrder(): CommandCategory[] {
  return import.meta.env.DEV
    ? [...COMMAND_CATEGORY_ORDER, "开发"]
    : COMMAND_CATEGORY_ORDER;
}

export interface PaletteCommand {
  id: string;
  title: string;
  category: CommandCategory;
  icon: LucideIcon;
  /** Extra match terms (English / aliases) so non-literal queries still hit. */
  keywords?: string[];
  /** Right-aligned key hint, rendered as a `<kbd>` (e.g. a global shortcut). */
  shortcut?: string;
  /** Right-aligned plain hint, e.g. the current value of a toggle. */
  hint?: string;
  /** When true the palette stays open after run. */
  keepOpen?: boolean;
  /** Perform the action. The palette closes itself after this runs unless {@link keepOpen}. */
  run: () => void;
}

/** Snapshot of UI state the command list reflects (so toggle hints / the active
 * theme stay accurate) plus the router's navigate. */
export interface CommandContext {
  navigate: NavigateFunction;
  theme: "light" | "dark" | "system";
  sidebarCollapsed: boolean;
  /**
   * Dev-only demo tapes from ``GET /v1/demo-tape`` when the server switch is on.
   * Absent / empty → no palette entry (zero product surface when replay is off).
   */
  demoTapes?: DemoTapeSummary[];
  /** Hide toolbox / conversation-admin commands (窄屏视口政策). */
  restrictNarrow?: boolean;
  /** Hide dark / system theme (窄屏 / Capacitor 仅浅色). */
  forceLightTheme?: boolean;
  /** Dev-only 窗口拍摄比例当前值，用于命令 hint。 */
  framePreset?: WindowFramePreset;
}

/**
 * Build the Tier 2 command list for the global palette.
 *
 * Pure data (no hooks) so the palette can rebuild it cheaply whenever the
 * reflected state changes; actions reach the stores via `getState()` / the
 * passed `navigate`. Grouped by {@link CommandCategory} at render time.
 */
export function buildPaletteCommands(ctx: CommandContext): PaletteCommand[] {
  const {
    navigate,
    theme,
    sidebarCollapsed,
    demoTapes = [],
    restrictNarrow = false,
    forceLightTheme = false,
    framePreset,
  } = ctx;
  const go = (path: string) => () => navigate(path);

  const commands: PaletteCommand[] = [
    // ---- 操作 (actions) ----
    {
      id: "new-conversation",
      title: "新建对话",
      category: "操作",
      icon: Plus,
      keywords: ["new", "chat", "compose", "xinjian", "duihua"],
      shortcut: chord("n"),
      run: () => startNewConversation(navigate),
    },
    // Dev-only 磁带回放：仅当服务端 DEMO_TAPE_REPLAY_ENABLED 且目录非空时注入。
    // 主入口 = 准备模式（空会话，用户亲自发消息开播）；立即开播为备选。
    ...demoTapes.flatMap((tape) => [
      {
        id: `demo-tape-${tape.id}`,
        title: `演示回放 · ${tape.title}`,
        category: "操作" as const,
        icon: Clapperboard,
        keywords: [
          "demo",
          "tape",
          "replay",
          "prepare",
          "yanshi",
          "huifang",
          "cidai",
          "zhunbei",
          tape.id,
          tape.title,
        ],
        hint: "开发 · 准备",
        run: () => void prepareDemoTapeAndOpen(tape.id, navigate),
      },
      {
        id: `demo-tape-${tape.id}-autostart`,
        title: `演示回放 · ${tape.title} · 立即开播`,
        category: "操作" as const,
        icon: Clapperboard,
        keywords: [
          "demo",
          "tape",
          "replay",
          "autostart",
          "yanshi",
          "huifang",
          "lijikai",
          tape.id,
          tape.title,
        ],
        hint: "开发 · 一键",
        run: () => void startDemoTapeAndOpen(tape.id, navigate),
      },
    ]),
    {
      id: "toggle-sidebar",
      title: sidebarCollapsed ? "展开侧栏" : "收起侧栏",
      category: "操作",
      icon: PanelLeft,
      keywords: ["sidebar", "toggle", "celan", "shoouqi"],
      shortcut: chord("b"),
      run: () => useSidebarStore.getState().toggleCollapsed(),
    },
    {
      id: "zoom-in",
      title: "放大界面",
      category: "操作",
      icon: ZoomIn,
      keywords: ["zoom", "in", "scale", "fangda", "suofang"],
      shortcut: chord("="),
      run: () => useUIStore.getState().nudgeZoom(1),
    },
    {
      id: "zoom-out",
      title: "缩小界面",
      category: "操作",
      icon: ZoomOut,
      keywords: ["zoom", "out", "scale", "suoxiao", "suofang"],
      shortcut: chord("-"),
      run: () => useUIStore.getState().nudgeZoom(-1),
    },
    {
      id: "zoom-reset",
      title: "恢复默认缩放",
      category: "操作",
      icon: RotateCcw,
      keywords: ["zoom", "reset", "default", "huifu", "moren"],
      shortcut: chord("0"),
      run: () => useUIStore.getState().resetZoom(),
    },
    {
      // Acts on the open conversation (导出对话). A draft has no server id yet, so
      // guard with a hint rather than silently no-op'ing.
      id: "export-conversation",
      title: "导出当前对话（Markdown）",
      category: "操作",
      icon: Download,
      keywords: ["export", "download", "daochu", "markdown", "md"],
      run: () => {
        const id = useConversationStore.getState().currentConversationId;
        if (!id) {
          notifyError("请先打开一个对话");
          return;
        }
        void exportConversation(id).catch((e) => notifyError(e, "导出失败"));
      },
    },
    {
      id: "share-conversation",
      title: "分享当前对话",
      category: "操作",
      icon: Share2,
      keywords: ["share", "link", "public", "fenxiang", "lianjie"],
      run: () => {
        const id = useConversationStore.getState().currentConversationId;
        if (!id) {
          notifyError("请先打开一个对话");
          return;
        }
        useShareStore.getState().open(id);
      },
    },
    {
      id: "open-workspace-terminal",
      title: "在终端打开工作区",
      category: "操作",
      icon: Terminal,
      keywords: [
        "terminal",
        "shell",
        "workspace",
        "zhongduan",
        "gongzuoqu",
        "bash",
      ],
      shortcut: chord("`"),
      run: () => {
        void openCurrentConversationTerminal();
      },
    },
    ...(hasLocalFiles()
      ? [
          {
            id: "connect-git",
            title: "从 Git 克隆",
            category: "操作" as const,
            icon: GitBranch,
            keywords: [
              "git",
              "clone",
              "cloud",
              "connect",
              "repo",
              "lianjie",
              "cangku",
              "project",
              "xiangmu",
              "kelong",
            ],
            hint: "推荐 · 云端浅克隆",
            run: () => {
              setComposerChannelPreference("cloud");
              useFoldersStore.getState().openConnectGit();
            },
          },
          {
            id: "borrow-to-cloud",
            title: "云上做完再写入",
            category: "操作" as const,
            icon: CloudUpload,
            keywords: [
              "borrow",
              "cloud",
              "writeback",
              "original",
              "yunshang",
              "xieru",
              "yuanjian",
              "zuowan",
            ],
            hint: "这一单在云上做，原件先不动",
            run: () => {
              setComposerChannelPreference("cloud");
              useFoldersStore.getState().openBorrowToCloud();
            },
          },
          {
            // §七：本机传统入口（打开本机文件夹）；履约由 openLocal 并行桶恢复。
            id: "open-local-project",
            title: "打开本机文件夹",
            category: "操作" as const,
            icon: HardDrive,
            keywords: [
              "local",
              "traditional",
              "folder",
              "open",
              "benji",
              "chuantong",
              "dakai",
              "bendi",
              "wenjianjia",
            ],
            hint: "本机传统 · 直改目录，≠离线",
            run: () => {
              setComposerChannelPreference("local_traditional");
              void pickAndOpenLocalFolder(navigate);
            },
          },
          {
            id: "grant-readonly-folder",
            title: "授权本机目录（只读）",
            category: "操作" as const,
            icon: FolderKey,
            keywords: [
              "grant",
              "readonly",
              "external",
              "desktop",
              "folder",
              "shouquan",
              "zhuomian",
              "quwai",
              "mulu",
            ],
            hint: "请在对话中说明目录",
            run: () => {
              // C1：禁止空白选目录；区外只读挂载改由对话 path / well_known 解析履约。
              notifyError(
                "请在对话中说明要授权的本机目录（命令面板不再打开系统选文件夹）",
              );
            },
          },
        ]
      : []),

    // ---- 前往 (navigation) ----
    {
      id: "nav-conversations",
      title: "全部对话",
      category: "前往",
      icon: MessagesSquare,
      keywords: ["conversations", "all", "duihua"],
      run: go("/conversations"),
    },
    {
      id: "nav-files",
      title: "文件",
      category: "前往",
      icon: Files,
      keywords: ["files", "workspace", "wenjian", "gongzuoqu"],
      run: go("/files"),
    },
    {
      id: "nav-messages",
      title: "消息",
      category: "前往",
      icon: Mail,
      keywords: ["messages", "im", "xiaoxi"],
      run: go("/messages"),
    },
    {
      id: "nav-toolbox",
      title: "工具箱",
      category: "前往",
      icon: Settings,
      keywords: ["toolbox", "tools", "gongju"],
      run: go("/toolbox"),
    },
    {
      // 我的 · 提示词。搜「技能」落到这里。
      id: "nav-guidelines",
      title: "交代",
      category: "前往",
      icon: ScrollText,
      keywords: [
        "guidelines",
        "prompt",
        "skills",
        "consult",
        "tishici",
        "jineng",
        "nengli",
        "技能",
        "提示词",
        "jiaodai",
      ],
      run: go(APP_PATHS.toolbox.guidelines),
    },
    {
      id: "nav-mcp",
      title: "MCP",
      category: "前往",
      icon: Unplug,
      keywords: ["mcp", "stdio", "插头"],
      run: go(APP_PATHS.toolbox.mcp),
    },
    {
      id: "nav-official",
      title: "官方",
      category: "前往",
      icon: ScrollText,
      keywords: [
        "official",
        "guidelines",
        "zhunze",
        "准则",
        "教法",
        "提示词",
        "开场工具",
        "出厂",
      ],
      run: go(`${APP_PATHS.toolbox.root}#tools`),
    },
    {
      id: "nav-store",
      title: "市场",
      category: "前往",
      icon: Store,
      keywords: [
        "store",
        "skill store",
        "shangdian",
        "商店",
        "上架",
        "安装",
        "jineng",
      ],
      run: go(APP_PATHS.toolbox.store),
    },
    {
      id: "nav-manual",
      title: "产品手册",
      category: "前往",
      icon: BookOpen,
      keywords: ["manual", "guide", "docs", "help", "shouce", "chanpin"],
      run: go("/toolbox/manual"),
    },
    {
      id: "nav-mechanism",
      title: "看懂协作（手册）",
      category: "前往",
      icon: Workflow,
      keywords: ["team", "mechanism", "graph", "tuandui", "xiezuo", "manual"],
      run: go("/toolbox/manual/mechanism?s=panorama"),
    },
    {
      id: "nav-settings",
      title: "设置",
      category: "前往",
      icon: Settings,
      keywords: ["settings", "shezhi", "more"],
      run: go("/more"),
    },
    {
      id: "nav-settings-model",
      title: "能力",
      category: "前往",
      icon: Cpu,
      keywords: [
        "settings",
        "model",
        "moxing",
        "nengli",
        "能力",
        "装配",
        "zhuangpei",
        "toolbox",
      ],
      run: go("/more/model"),
    },
    {
      id: "nav-settings-models",
      title: "设置 · 模型",
      category: "前往",
      icon: Cpu,
      keywords: [
        "settings",
        "models",
        "moxing",
        "picker",
        "隐藏",
        "yincang",
        "模型",
      ],
      run: go("/more/models"),
    },
    {
      id: "nav-settings-providers",
      title: "设置 · 服务商",
      category: "前往",
      icon: KeyRound,
      keywords: [
        "settings",
        "providers",
        "byok",
        "key",
        "api",
        "fuwuoshang",
        "服务商",
      ],
      run: go("/more/providers"),
    },
    {
      id: "nav-settings-search",
      title: "设置 · 联网搜索",
      category: "前往",
      icon: Globe,
      keywords: [
        "settings",
        "search",
        "web",
        "deepseek",
        "lianwang",
        "sousuo",
        "联网",
        "搜索",
      ],
      run: go("/more/search"),
    },
    {
      id: "nav-settings-account",
      title: "设置 · 账户",
      category: "前往",
      icon: UserCog,
      keywords: [
        "settings",
        "account",
        "profile",
        "password",
        "zhanghu",
        "mima",
        "ziliao",
      ],
      run: go("/more/account"),
    },
    {
      id: "nav-settings-usage",
      title: "设置 · 用量",
      category: "前往",
      icon: BarChart3,
      keywords: ["settings", "usage", "billing", "yongliang"],
      run: go("/more/usage"),
    },
    {
      // 原「设置 · 外观」(/more/appearance)：保留 appearance / 外观 关键词，
      // 老肌肉记忆搜「外观」仍落到这里。
      id: "nav-settings-general",
      title: "设置 · 通用",
      category: "前往",
      icon: SlidersHorizontal,
      keywords: [
        "settings",
        "general",
        "appearance",
        "theme",
        "tongyong",
        "waiguan",
        "外观",
        "主题",
        "字号",
        "缩放",
        "zoom",
        "font",
        "发送",
        "通知",
        "enter",
        "send",
      ],
      run: go("/more/general"),
    },
    {
      id: "nav-settings-shortcuts",
      title: "设置 · 快捷键",
      category: "前往",
      icon: Keyboard,
      keywords: ["settings", "shortcuts", "keys", "kuaijiejian"],
      run: go("/more/shortcuts"),
    },
    {
      id: "nav-settings-about",
      title: "设置 · 关于",
      category: "前往",
      icon: Info,
      keywords: ["settings", "about", "version", "guanyu"],
      run: go("/more/about"),
    },
    {
      id: "nav-settings-subscription",
      title: "设置 · 订阅",
      category: "前往",
      icon: CreditCard,
      keywords: [
        "settings",
        "subscription",
        "dingyue",
        "redeem",
        "duihuan",
        "兑换",
        "赞助",
        "购买",
      ],
      run: go("/more/subscription"),
    },

    // ---- 主题 (theme) ----
    {
      id: "theme-light",
      title: "浅色主题",
      category: "主题",
      icon: Sun,
      keywords: ["theme", "light", "qiansec", "qianse"],
      hint: theme === "light" ? "当前" : undefined,
      run: () => useUIStore.getState().setTheme("light"),
    },
    {
      id: "theme-dark",
      title: "深色主题",
      category: "主题",
      icon: Moon,
      keywords: ["theme", "dark", "shense"],
      hint: theme === "dark" ? "当前" : undefined,
      run: () => useUIStore.getState().setTheme("dark"),
    },
    {
      id: "theme-system",
      title: "跟随系统",
      category: "主题",
      icon: Monitor,
      keywords: ["theme", "system", "auto", "genxisuitong"],
      hint: theme === "system" ? "当前" : undefined,
      run: () => useUIStore.getState().setTheme("system"),
    },
  ];

  // Dev-only doorway: 前端预览 harness（无侧栏一级入口）。
  if (import.meta.env.DEV) {
    commands.push({
      id: "nav-preview",
      title: "前端预览（开发）",
      category: "前往",
      icon: FlaskConical,
      keywords: [
        "preview",
        "fixtures",
        "yulan",
        "qianduan",
        "dev",
        "harness",
        "ai",
        "xunjian",
        "巡检",
        "截图",
      ],
      hint: "离线回放 AI 态",
      run: go("/preview"),
    });
    const pickScheme = (group: string, id: string) => {
      setSchemeId(group, id);
      setSchemePanelOpen(true);
    };
    commands.push({
      id: "dev-scheme-open",
      title: "界面方案",
      category: "开发",
      icon: FlaskConical,
      keywords: ["scheme", "fangan", "kaiguan", "switch"],
      hint: "右上角面板",
      shortcut: chord("."),
      run: () => setSchemePanelOpen(true),
    });
    for (const group of listSchemeGroups()) {
      const active = getSchemeId(group.id);
      commands.push({
        id: `dev-scheme-${group.id}-current`,
        title: `${group.label}：现行`,
        category: "开发",
        icon: FlaskConical,
        keywords: ["scheme", "fangan", "xianxing", group.id],
        hint: active === CURRENT_SCHEME_ID ? "当前" : undefined,
        shortcut: chord("."),
        run: () => pickScheme(group.id, CURRENT_SCHEME_ID),
      });
      for (const scheme of listSchemes(group.id)) {
        commands.push({
          id: `dev-scheme-${scheme.group}-${scheme.id}`,
          title: `${group.label}：${scheme.label}`,
          category: "开发",
          icon: FlaskConical,
          keywords: ["scheme", "fangan", scheme.id, scheme.label],
          hint: active === scheme.id ? "当前" : undefined,
          shortcut: chord("."),
          run: () => pickScheme(scheme.group, scheme.id),
        });
      }
    }
    if (hasWindowControls()) {
      const applyFrame = (preset: WindowFramePreset) => {
        if (typeof window === "undefined") return;
        void window.windowApi?.applyFramePreset?.(preset);
      };
      commands.push({
        id: "dev-window-frame-free",
        title: "窗口拍摄：自由缩放",
        category: "开发",
        icon: Scan,
        keywords: [
          "obs",
          "frame",
          "aspect",
          "paishe",
          "bili",
          "luping",
          "free",
          "ziyou",
        ],
        hint: framePreset === "free" ? "当前" : undefined,
        run: () => applyFrame("free"),
      });
      for (const preset of WINDOW_FRAME_PRESETS) {
        commands.push({
          id: `dev-window-frame-${preset.id}`,
          title: `窗口拍摄：${preset.label}`,
          category: "开发",
          icon: Scan,
          keywords: [
            "obs",
            "frame",
            "aspect",
            "paishe",
            "bili",
            "luping",
            preset.id,
            preset.label,
          ],
          hint: framePreset === preset.id ? "当前" : undefined,
          run: () => applyFrame(preset.id),
        });
      }
    }
  }

  return commands.filter(
    (cmd) =>
      !isNarrowHiddenPaletteId(cmd.id, { restrictNarrow, forceLightTheme }),
  );
}

/** Local substring matcher: every whitespace-separated token of the query must
 * appear in the command's title / category / keywords (case-insensitive).
 * Commands are filtered client-side — unlike entity hits, which come pre-filtered
 * from the backend search. */
export function commandMatches(cmd: PaletteCommand, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay =
    `${cmd.title} ${cmd.category} ${(cmd.keywords ?? []).join(" ")}`.toLowerCase();
  return q
    .split(/\s+/)
    .filter(Boolean)
    .every((token) => hay.includes(token));
}
