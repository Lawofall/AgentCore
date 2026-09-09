/**
 * 手册节 ID 集中注册表 —— 功能现场深链 / JumpLink / 内容源共用。
 *
 * 信息架构：
 * - intro: what / mindset / quickstart
 * - collaboration: briefing / progress / checkpoint / autonomy / debate /
 *   control / memory / workflow
 * - mechanism: live / legend / panorama / scenarios
 * - reference: tools / workspace / settings / faq / troubleshooting /
 *   privacy / glossary
 *
 * 旧节 ID 见 MANUAL_SECTION_ALIASES（深链滚到新节，勿裸删）。
 */

import { MANUAL_CHAPTER_PATHS, type ManualChapterId } from "./paths";

export const MANUAL_SECTION_IDS = {
  intro: {
    what: "what",
    mindset: "mindset",
    quickstart: "quickstart",
  },
  collaboration: {
    briefing: "briefing",
    progress: "progress",
    checkpoint: "checkpoint",
    autonomy: "autonomy",
    debate: "debate",
    /** 中途插手（含带现场续派） */
    control: "control",
    memory: "memory",
    /** 把一轮协作存成工作流，之后微调 / 复跑 / 设为定时 */
    workflow: "workflow",
  },
  mechanism: {
    live: "live",
    legend: "legend",
    /** 从发消息到收答案（原 panorama + turnflow） */
    panorama: "panorama",
    scenarios: "scenarios",
  },
  reference: {
    tools: "tools",
    workspace: "workspace",
    settings: "settings",
    faq: "faq",
    troubleshooting: "troubleshooting",
    privacy: "privacy",
    glossary: "glossary",
  },
} as const;

/**
 * 旧节 ID → 现行节 ID（书签 / 旧深链兼容）。
 * 别名本身不出现在内容源 sections 列表。
 */
export const MANUAL_SECTION_ALIASES: Record<string, string> = {
  "collab-overview": MANUAL_SECTION_IDS.collaboration.briefing,
  roles: MANUAL_SECTION_IDS.intro.mindset,
  continuation: MANUAL_SECTION_IDS.collaboration.control,
  turnflow: MANUAL_SECTION_IDS.mechanism.panorama,
  chat: MANUAL_SECTION_IDS.reference.faq,
  automation: MANUAL_SECTION_IDS.collaboration.workflow,
};

export type IntroSectionId =
  (typeof MANUAL_SECTION_IDS.intro)[keyof typeof MANUAL_SECTION_IDS.intro];
export type CollaborationSectionId =
  (typeof MANUAL_SECTION_IDS.collaboration)[keyof typeof MANUAL_SECTION_IDS.collaboration];
export type MechanismSectionId =
  (typeof MANUAL_SECTION_IDS.mechanism)[keyof typeof MANUAL_SECTION_IDS.mechanism];
export type ReferenceSectionId =
  (typeof MANUAL_SECTION_IDS.reference)[keyof typeof MANUAL_SECTION_IDS.reference];

export type ManualSectionId =
  | IntroSectionId
  | CollaborationSectionId
  | MechanismSectionId
  | ReferenceSectionId;

/** 别名归一到现行节 ID。 */
export function resolveCanonicalSectionId(sectionId: string): string {
  return MANUAL_SECTION_ALIASES[sectionId] ?? sectionId;
}

/** 节 ID → 所属章（供 JumpLink 跨节回退导航）；含别名。 */
const SECTION_OWNER: Record<string, ManualChapterId> = (() => {
  const map: Record<string, ManualChapterId> = {};
  for (const [chapterId, sections] of Object.entries(MANUAL_SECTION_IDS) as [
    ManualChapterId,
    Record<string, string>,
  ][]) {
    for (const id of Object.values(sections)) {
      map[id] = chapterId;
    }
  }
  for (const [alias, canonical] of Object.entries(MANUAL_SECTION_ALIASES)) {
    const chapter = map[canonical];
    if (chapter) map[alias] = chapter;
  }
  return map;
})();

/** 章 + 节 → 深链 path（含 `?s=`）；节 ID 会归一到现行 ID。 */
export function manualHref(
  chapter: ManualChapterId,
  section: ManualSectionId | string,
): string {
  const canonical = resolveCanonicalSectionId(section);
  return `${MANUAL_CHAPTER_PATHS[chapter]}?s=${canonical}`;
}

/** 仅知节 ID 时解析深链；未知节返回 null。href 使用现行节 ID。 */
export function resolveSectionHref(sectionId: string): string | null {
  const canonical = resolveCanonicalSectionId(sectionId);
  const chapter = SECTION_OWNER[canonical];
  if (!chapter) return null;
  return manualHref(chapter, canonical);
}

/** 节是否在注册表中（含别名）。 */
export function isRegisteredSectionId(sectionId: string): boolean {
  return sectionId in SECTION_OWNER;
}

export function chapterOfSection(
  sectionId: string,
): ManualChapterId | undefined {
  return SECTION_OWNER[resolveCanonicalSectionId(sectionId)];
}
