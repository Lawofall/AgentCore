/**
 * 应用内路由常量 —— 手册内容源 / SettingsTable / 深链唯一真相源。
 * 手册内禁止手写路由字符串，一律 import 本文件。
 */

export const APP_PATHS = {
  files: "/files",
  toolbox: {
    root: "/toolbox",
    mine: {
      skills: "/toolbox/mine/skills",
      tools: "/toolbox/mine/tools",
      creation: "/toolbox/mine/creation",
      mcp: "/toolbox/mine/mcp",
      automations: "/toolbox/mine/automations",
      workflows: "/toolbox/mine/workflows",
    },
    market: "/toolbox/market",
    /** Canonical aliases — 旧名仍可用，指向现行壳。 */
    tools: "/toolbox/mine/tools",
    guidelines: "/toolbox/mine/skills",
    /** Prompt catalog right pane = cross-conversation「最近更新」feed. */
    guidelinesUpdates: "/toolbox/mine/skills?updates=1",
    store: "/toolbox/market",
    /** 工具页图鉴（插头卡在同一网格；旧 `#/toolbox/connectors` / `mine/mcp` 收向这里）。 */
    connectors: "/toolbox/mine/tools",
    /** 旧书签，路由收向工作流列表。 */
    automations: {
      root: "/toolbox/mine/automations",
      inbox: "/toolbox/mine/automations?inbox=1",
    },
    workflows: {
      root: "/toolbox/mine/workflows",
      edit: (id: string) => `/toolbox/workflows/${id}`,
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
    legal: {
      terms: "/more/legal/terms",
      privacy: "/more/legal/privacy",
    },
  },
} as const;

/** Deep pages that leave the two-page shell (画布 / 手册). */
export const TOOLBOX_PAGE_BACK = {
  to: APP_PATHS.toolbox.mine.skills,
  label: "工具箱",
} as const;

export const TOOLBOX_WORKFLOWS_BACK = {
  to: APP_PATHS.toolbox.workflows.root,
  label: "工作流",
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
