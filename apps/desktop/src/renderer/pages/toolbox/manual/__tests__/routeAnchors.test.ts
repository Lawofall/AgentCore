import { MANUAL_HELP } from "@/components/ManualHelpLink";
import { describe, expect, it } from "vitest";
import { CONTENT_CHAPTERS } from "../content";
import { isKnownAppRoute, parseGoTarget } from "../gates/appRoutes";
import {
  chapterSectionIds,
  collectContentLinks,
  pathToChapterId,
} from "../gates/collectLinks";
import {
  isRegisteredSectionId,
  resolveCanonicalSectionId,
} from "../sectionIds";

function assertGoOrSettings(
  to: string,
  where: string,
  sectionsByChapter: Map<string, Set<string>>,
  pathChapter: Map<string, string>,
  failures: string[],
): void {
  const { pathname, section } = parseGoTarget(to);
  if (!isKnownAppRoute(pathname)) {
    failures.push(`${where}: 未知路由「${pathname}」（to=${to}）`);
    return;
  }
  if (section == null) return;
  const chapterId = pathChapter.get(pathname);
  if (!chapterId) {
    failures.push(
      `${where}: 带 ?s=${section} 但「${pathname}」不是手册章 path`,
    );
    return;
  }
  const canonical = resolveCanonicalSectionId(section);
  const ids = sectionsByChapter.get(chapterId);
  if (!ids?.has(canonical)) {
    failures.push(
      `${where}: 章 ${chapterId} 无 section id「${section}」→「${canonical}」（to=${to}）`,
    );
  }
}

describe("manual route / anchor gates", () => {
  it("内容源 go / jump / settingsRows.to 对齐真实路由与 section id", () => {
    const links = collectContentLinks();
    expect(links.length).toBeGreaterThan(5);

    const sectionsByChapter = chapterSectionIds();
    const pathChapter = pathToChapterId();
    const failures: string[] = [];

    for (const link of links) {
      if (link.kind === "jump") {
        if (!isRegisteredSectionId(link.to)) {
          failures.push(
            `${link.where}: jump 锚点「${link.to}」不在 sectionIds 注册表`,
          );
        }
        continue;
      }
      // go | settings
      assertGoOrSettings(
        link.to,
        link.where,
        sectionsByChapter,
        pathChapter,
        failures,
      );
    }

    expect(failures, failures.join("\n")).toEqual([]);
  });

  it("MANUAL_HELP 深链对齐手册章与 section", () => {
    const sectionsByChapter = chapterSectionIds();
    const pathChapter = pathToChapterId();
    const failures: string[] = [];

    for (const [key, to] of Object.entries(MANUAL_HELP)) {
      assertGoOrSettings(
        to,
        `MANUAL_HELP.${key}`,
        sectionsByChapter,
        pathChapter,
        failures,
      );
    }

    expect(Object.keys(MANUAL_HELP).sort()).toEqual([
      "autonomy",
      "checkpoint",
      "control",
      "debate",
    ]);
    expect(failures, failures.join("\n")).toEqual([]);
  });

  it("内容源 section id 均在集中注册表内", () => {
    const missing: string[] = [];
    for (const chapter of CONTENT_CHAPTERS) {
      for (const section of chapter.sections) {
        if (!isRegisteredSectionId(section.id)) {
          missing.push(`${chapter.id}/${section.id}`);
        }
      }
    }
    expect(missing).toEqual([]);
  });

  it("旧节 ID 别名归一到现行节", () => {
    expect(resolveCanonicalSectionId("continuation")).toBe("control");
    expect(resolveCanonicalSectionId("turnflow")).toBe("panorama");
    expect(resolveCanonicalSectionId("collab-overview")).toBe("briefing");
    expect(resolveCanonicalSectionId("roles")).toBe("mindset");
    expect(resolveCanonicalSectionId("chat")).toBe("faq");
    expect(isRegisteredSectionId("continuation")).toBe(true);
    expect(isRegisteredSectionId("control")).toBe(true);
  });

  it("不把未落地的 /explore 空壳当已知用户路由", () => {
    expect(isKnownAppRoute("/explore")).toBe(false);
  });
});
