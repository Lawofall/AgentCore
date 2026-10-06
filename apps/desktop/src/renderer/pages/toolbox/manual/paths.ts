/**
 * 应用内路由常量 —— 手册内容源 / SettingsTable / 深链唯一真相源。
 * 手册内禁止手写路由字符串，一律 import 本文件。
 */

export const APP_PATHS = {
  files: "/files",
  toolbox: {
    root: "/toolbox",
    /** 装配「总」节：换份、星标、摘要。 */
    overview: "/toolbox/overview",
    /** 装配「模型」节。 */
    model: "/toolbox/model",
    /** 装配「工具」节。旧 `/toolbox/mine/tools` 仍收向交代。 */
    toolSwitches: "/toolbox/tools",
    /** 装配「信封」节。 */
    envelope: "/toolbox/envelope",
    mine: {
      skills: "/toolbox/mine/skills",
      tools: "/toolbox/mine/tools",
      creation: "/toolbox/mine/creation",
      automations: "/toolbox/mine/automations",
      workflows: "/toolbox/mine/workflows",
    },
    market: "/toolbox/market",
    /** 旧官方深页。落到组装页，?tool= / ?skill= 打开读卡。 */
    official: "/toolbox/official",
    /** 旧说明书深页书签；?tool= / ?skill= 落到组装页读卡，其余收到交代。 */
    guides: "/toolbox/guides",
    /** Canonical aliases — 旧名仍可用，指向现行壳。 */
    tools: "/toolbox/mine/tools",
    guidelines: "/toolbox/mine/skills",
    store: "/toolbox/market",
    /** 本机 MCP。 */
    mcp: "/toolbox/mcp",
    /** 旧书签，路由收向提示词。 */
    automations: {
      root: "/toolbox/mine/automations",
      inbox: "/toolbox/mine/automations?inbox=1",
    },
    manual: {
      root: "/toolbox/manual",
      intro: "/toolbox/manual/intro",
      collaboration: "/toolbox/manual/collaboration",
      mechanism: "/toolbox/manual/mechanism",
      reference: "/toolbox/manual/reference",
    },
  },
  more: {
    model: "/more/model",
    providers: "/more/providers",
    usage: "/more/usage",
    general: "/more/general",
    shortcuts: "/more/shortcuts",
    /** Legacy; `#/more/notices` redirects to the IM official chat. */
    notices: "/more/notices",
    about: "/more/about",
    sponsor: "/more/sponsor",
    legal: {
      terms: "/more/legal/terms",
      privacy: "/more/legal/privacy",
    },
  },
} as const;

/** Deep pages that leave the two-page shell (画布 / 手册). */
export const TOOLBOX_PAGE_BACK = {
  to: `${APP_PATHS.toolbox.root}#prompts`,
  label: "工具箱",
} as const;

export type ManualChapterId =
  | "intro"
  | "collaboration"
  | "mechanism"
  | "reference";

export const MANUAL_CHAPTER_PATHS: Record<ManualChapterId, string> = {
  intro: APP_PATHS.toolbox.manual.intro,
  collaboration: APP_PATHS.toolbox.manual.collaboration,
  mechanism: APP_PATHS.toolbox.manual.mechanism,
  reference: APP_PATHS.toolbox.manual.reference,
};
