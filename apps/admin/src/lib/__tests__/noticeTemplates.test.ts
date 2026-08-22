import { describe, expect, it } from "vitest";
import {
  NOTICE_TEMPLATES,
  RELEASE_CHECK_UPDATE_CTA,
  buildFromSlots,
  emptySlotValues,
  surfacePublishHint,
  templateToFormSeed,
} from "../noticeTemplates";

describe("noticeTemplates", () => {
  it("exposes operational templates with required fields", () => {
    expect(NOTICE_TEMPLATES).toHaveLength(12);
    for (const t of NOTICE_TEMPLATES) {
      expect(t.id).toBeTruthy();
      expect(t.title.trim().length).toBeGreaterThan(0);
      expect(t.body.trim().length).toBeGreaterThan(0);
      expect(t.slots.length).toBeGreaterThan(0);
      expect(["critical", "high", "normal"]).toContain(t.severity);
      expect(["banner", "inbox", "both", "modal"]).toContain(t.surface);
      expect(["once", "never"]).toContain(t.dismiss_policy);
      expect(["service", "article", undefined]).toContain(t.card_template);
    }
  });

  it("defaults ops templates to service; article/changelog use article", () => {
    for (const id of [
      "hotfix",
      "release",
      "maintenance",
      "policy",
      "quota_unavailable",
      "quota_platform_restored",
      "outage",
      "feature",
      "security",
      "campaign",
    ]) {
      const t = NOTICE_TEMPLATES.find((x) => x.id === id)!;
      expect(t.card_template ?? "service").toBe("service");
    }
    expect(NOTICE_TEMPLATES.find((x) => x.id === "article")!.card_template).toBe(
      "article",
    );
    expect(
      NOTICE_TEMPLATES.find((x) => x.id === "changelog")!.card_template,
    ).toBe("article");
  });

  it("never pairs modal with dismiss=never", () => {
    for (const t of NOTICE_TEMPLATES) {
      if (t.surface === "modal") {
        expect(t.dismiss_policy).toBe("once");
      }
    }
  });

  it("release/hotfix seed check-update CTA", () => {
    for (const id of ["hotfix", "release"]) {
      const seed = templateToFormSeed(NOTICE_TEMPLATES.find((x) => x.id === id)!);
      expect(seed.cta_label).toBe(RELEASE_CHECK_UPDATE_CTA.cta_label);
      expect(seed.cta_url).toBe(RELEASE_CHECK_UPDATE_CTA.cta_url);
      expect(seed.card_template).toBe("service");
    }
  });

  it("templateToFormSeed copies recommended fields and clears window", () => {
    const maintenance = NOTICE_TEMPLATES.find((x) => x.id === "maintenance")!;
    const seed = templateToFormSeed(maintenance);
    expect(seed.title).toBe(maintenance.title);
    expect(seed.severity).toBe(maintenance.severity);
    expect(seed.cta_label).toBe("");
    expect(seed.end_at).toBe("");
    expect(seed.card_template).toBe("service");
    expect(seed.summary).toBe("");
    expect(seed.cover_url).toBe("");
  });

  it("quota_unavailable seeds Key-access copy without CTA", () => {
    const t = NOTICE_TEMPLATES.find((x) => x.id === "quota_unavailable")!;
    expect(t).toBeTruthy();
    const seed = templateToFormSeed(t);
    expect(seed.title).toBe("平台额度暂时不可用 · 请接入自己的 Key");
    expect(seed.body).toContain("平台提供的额度暂时不可用");
    expect(seed.body).toContain("设置 · 服务商");
    expect(seed.body).toContain("接入自己的 Key");
    expect(seed.body).toContain("AgentCore 官方");
    expect(seed.body).not.toMatch(/注册|充值/);
    expect(seed.cta_label).toBe("");
    expect(seed.cta_url).toBe("");
    expect(seed.severity).toBe("high");
    expect(seed.surface).toBe("both");
    expect(seed.dismiss_policy).toBe("once");
    const withNote = buildFromSlots(t, { note: "预计明日恢复" });
    expect(withNote.body).toContain("补充：预计明日恢复");
  });

  it("quota_platform_restored seeds Flash restore copy via OpenCode Go", () => {
    const t = NOTICE_TEMPLATES.find((x) => x.id === "quota_platform_restored")!;
    expect(t).toBeTruthy();
    const seed = templateToFormSeed(t);
    expect(seed.title).toBe("平台额度已恢复 · 当前仅 DeepSeek V4 Flash");
    expect(seed.body).toContain("内测期提供测试额度");
    expect(seed.body).toContain("OpenCode Go");
    expect(seed.body).toContain("DeepSeek V4 Flash");
    expect(seed.body).toContain("非免费档");
    expect(seed.body).toContain("zero-retention");
    expect(seed.body).toContain("零留存");
    expect(seed.body).toContain("2026-08-31");
    expect(seed.body).toContain("按月续约");
    expect(seed.body).toContain("非永久承诺");
    expect(seed.body).toContain("设置 · 服务商");
    expect(seed.body).not.toMatch(/Flash Free|限时免费|OpenCode Zen|模型配置/);
    expect(seed.body).not.toMatch(/送\s*\d+\s*元/);
    expect(seed.surface).toBe("both");
    expect(seed.severity).toBe("normal");
    expect(t.endHint).toBe("发前先归档进行中的 quota_unavailable");
    const withNote = buildFromSlots(t, { note: "额度数字不变" });
    expect(withNote.body).toContain("补充：额度数字不变");
  });

  it("buildFromSlots fills hotfix copy from slot values", () => {
    const hotfix = NOTICE_TEMPLATES.find((t) => t.id === "hotfix")!;
    const built = buildFromSlots(hotfix, {
      time: "14:30",
      summary: "修复消息发送超时",
    });
    expect(built.title).toBe(
      "约 14:30 更新 · 请按需规划好时间 · 提前停止使用 AI 功能",
    );
    expect(built.body).toContain("今天约 14:30");
    expect(built.body).toContain("修复消息发送超时");
    expect(built.summary).toBeUndefined();
  });

  it("buildFromSlots keeps skeleton when slots empty", () => {
    const hotfix = NOTICE_TEMPLATES.find((t) => t.id === "hotfix")!;
    const built = buildFromSlots(hotfix, emptySlotValues(hotfix));
    expect(built.title).toContain("HH:MM");
    expect(built.body).toContain("一句话变更摘要");
  });

  it("buildFromSlots formats release highlights as numbered lines", () => {
    const release = NOTICE_TEMPLATES.find((t) => t.id === "release")!;
    const built = buildFromSlots(release, {
      version: "0.4.2",
      time: "10:00",
      highlights:
        "消息编辑\n撤回优化\n空桌少一层文件夹\n附件可重试\n多余行应被截断\n不会出现",
    });
    expect(built.title).toBe(
      "约 10:00 发版 · 请按需规划好时间 · 提前停止使用 AI 功能",
    );
    expect(built.body).toContain("1. 消息编辑");
    expect(built.body).toContain("2. 撤回优化");
    expect(built.body).toContain("3. 空桌少一层文件夹");
    expect(built.body).toContain("4. 附件可重试");
    expect(built.body).toContain("5. 多余行应被截断");
    expect(built.body).not.toContain("不会出现");
  });

  it("article/changelog build returns summary for card face", () => {
    const article = NOTICE_TEMPLATES.find((t) => t.id === "article")!;
    const built = buildFromSlots(article, {
      title: "协作图入门",
      summary: "三分钟看懂协作图",
      body: "正文很长…",
    });
    expect(built.title).toBe("协作图入门");
    expect(built.summary).toBe("三分钟看懂协作图");
    expect(built.body).toBe("正文很长…");
    const seed = templateToFormSeed(article);
    expect(seed.card_template).toBe("article");
    expect(seed.summary.length).toBeGreaterThan(0);

    const changelog = NOTICE_TEMPLATES.find((t) => t.id === "changelog")!;
    const ch = buildFromSlots(changelog, {
      version: "0.5.0",
      summary: "消息与协作改进",
      highlights: "消息编辑\n协作图预览",
    });
    expect(ch.title).toBe("版本亮点 · 0.5.0");
    expect(ch.summary).toBe("消息与协作改进");
    expect(ch.body).toContain("1. 消息编辑");
    expect(templateToFormSeed(changelog).cta_url).toBe(
      RELEASE_CHECK_UPDATE_CTA.cta_url,
    );
  });

  it("surfacePublishHint warns on invalid modal+never", () => {
    expect(surfacePublishHint("modal", "never")).toMatch(/仅支持/);
    expect(surfacePublishHint("both", "once")).toMatch(/横幅/);
    expect(surfacePublishHint("both", "once")).toMatch(/官方/);
  });
});
