/**
 * 应用内路由常量 —— 手册内容源 / SettingsTable / 深链唯一真相源。
 * 手册内禁止手写路由字符串，一律 import 本文件。
 */

export const APP_PATHS = {
  files: "/files",
  toolbox: {
    root: "/toolbox",
    tools: "/toolbox/tools",
    guidelines: "/toolbox/guidelines",
    store: "/toolbox/store",
    connectors: "/toolbox/connectors",
    automations: {
      root: "/toolbox/automations",
      inbox: "/toolbox/automations/inbox",
    },
    workflows: {
      root: "/toolbox/workflows",
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
    feedback: "/more/feedback",
    /** Legacy; `#/more/notices` redirects to the IM official chat. */
    notices: "/more/notices",
    about: "/more/about",
    legal: {
      terms: "/more/legal/terms",
      privacy: "/more/legal/privacy",
    },
  },
} as const;

/** Toolbox subpage back-link for `PageHeader`. */
export const TOOLBOX_PAGE_BACK = {
  to: APP_PATHS.toolbox.root,
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
