/**
 * 应用静态路由清单 —— 与 `renderer/router.tsx` 对齐。
 *
 * 不便从 JSX createHashRouter 树自动解析，故维护显式清单。
 * 增删用户可达 path 时：先改 router.tsx，再同步本表与 `paths.ts`。
 */

import { APP_PATHS } from "../paths";

/** hash router 路径（无 `#`），含手册四章与设置子页等手册会链到的目标。 */
export const APP_STATIC_ROUTES: readonly string[] = [
  "/",
  "/conversations",
  APP_PATHS.files,
  "/whiteboard",
  "/messages",
  APP_PATHS.toolbox.root,
  APP_PATHS.toolbox.tools,
  APP_PATHS.toolbox.guidelines,
  APP_PATHS.toolbox.connectors,
  APP_PATHS.toolbox.automations.root,
  APP_PATHS.toolbox.automations.inbox,
  APP_PATHS.toolbox.workflows.root,
  APP_PATHS.toolbox.manual.root,
  APP_PATHS.toolbox.manual.intro,
  APP_PATHS.toolbox.manual.collaboration,
  APP_PATHS.toolbox.manual.mechanism,
  APP_PATHS.toolbox.manual.reference,
  "/preview",
  "/preview/whiteboard",
  "/preview/ask-commence",
  "/preview/onboarding",
  "/preview/conversations",
  "/preview/files",
  "/more",
  APP_PATHS.more.model,
  APP_PATHS.more.providers,
  "/more/account",
  "/more/messages",
  APP_PATHS.more.usage,
  APP_PATHS.more.general,
  APP_PATHS.more.shortcuts,
  APP_PATHS.more.feedback,
  APP_PATHS.more.about,
  APP_PATHS.more.legal.terms,
  APP_PATHS.more.legal.privacy,
] as const;

const ROUTE_SET = new Set(APP_STATIC_ROUTES);

export function isKnownAppRoute(pathname: string): boolean {
  if (ROUTE_SET.has(pathname)) return true;
  // 带动态段的前缀（手册内容源一般不链这些；留给将来扩展）
  if (/^\/conversations\/[^/]+$/.test(pathname)) return true;
  if (/^\/conversations\/[^/]+\/turn\/[^/]+$/.test(pathname)) return true;
  if (/^\/whiteboard\/[^/]+$/.test(pathname)) return true;
  if (/^\/messages\/[^/]+$/.test(pathname)) return true;
  if (/^\/more\/legal\/[^/]+$/.test(pathname)) return true;
  if (/^\/toolbox\/workflows\/[^/]+$/.test(pathname)) return true;
  return false;
}

export function parseGoTarget(to: string): {
  pathname: string;
  section: string | null;
} {
  const q = to.indexOf("?");
  const pathname = q >= 0 ? to.slice(0, q) : to;
  let section: string | null = null;
  if (q >= 0) {
    const params = new URLSearchParams(to.slice(q + 1));
    section = params.get("s");
  }
  return { pathname, section };
}
