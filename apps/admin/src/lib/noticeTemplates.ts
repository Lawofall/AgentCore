/**
 * 产品公告运营模板（与 docs/05-平台与运维/产品公告文案模板.md 对齐）。
 * 套用后填入草稿表单；可用「快捷填写」槽位生成标题/正文。
 */

export type NoticeTemplateSeverity = "critical" | "high" | "normal";
export type NoticeTemplateSurface = "banner" | "inbox" | "both" | "modal";
export type NoticeTemplateDismiss = "once" | "never";
export type NoticeCardTemplate = "service" | "article";

/** 发版/热修默认 CTA：应用内「关于」可检查更新（桌面 openNoticeCta 认 `/…`）。 */
export const RELEASE_CHECK_UPDATE_CTA = {
  cta_label: "检查更新",
  cta_url: "/more/about",
} as const;

export type NoticeTemplateSlot = {
  key: string;
  label: string;
  placeholder?: string;
  /** 多行输入（亮点、说明等） */
  multiline?: boolean;
};

export type NoticeTemplateBuild = {
  title: string;
  body: string;
  /** article 卡面摘要；service 模板勿返回 */
  summary?: string;
};

export type NoticeTemplate = {
  id: string;
  label: string;
  description: string;
  /** 未填槽位时的骨架标题 */
  title: string;
  /** 未填槽位时的骨架正文 */
  body: string;
  severity: NoticeTemplateSeverity;
  surface: NoticeTemplateSurface;
  dismiss_policy: NoticeTemplateDismiss;
  /** 官方号双模板；省略 = service */
  card_template?: NoticeCardTemplate;
  /** article 套用时的摘要骨架（可空；发布前须填） */
  summary?: string;
  slots: readonly NoticeTemplateSlot[];
  /** 维护/故障类建议设结束时间的提示 */
  endHint?: string;
  /** 套用时预填 CTA（可选） */
  cta_label?: string;
  cta_url?: string;
  build: (v: Record<string, string>) => NoticeTemplateBuild;
};

function s(v: Record<string, string>, key: string, fallback: string): string {
  const raw = v[key]?.trim();
  return raw || fallback;
}

export const NOTICE_TEMPLATES: readonly NoticeTemplate[] = [
  {
    id: "hotfix",
    label: "后端热修",
    description: "短更新 · 横幅 + IM",
    title: "约 HH:MM 更新 · 请按需规划好时间 · 提前停止使用 AI 功能",
    body: `我们将于今天约 HH:MM 进行一次系统更新，预计 1–3 分钟。

更新期间 AI 功能不可用。请按需规划好时间 · 提前停止使用 AI 功能，以免进行中的对话或任务被中断。

更新完成后刷新即可；一般无需重装客户端。
本次：…（一句话变更摘要；多条用「；」分隔）

若结束后仍异常，打开消息页「AgentCore 官方」或稍后重试。`,
    severity: "high",
    surface: "both",
    dismiss_policy: "once",
    card_template: "service",
    cta_label: RELEASE_CHECK_UPDATE_CTA.cta_label,
    cta_url: RELEASE_CHECK_UPDATE_CTA.cta_url,
    slots: [
      { key: "time", label: "预计时间", placeholder: "如 14:30" },
      {
        key: "summary",
        label: "变更摘要",
        placeholder: "用户能感知的一句话；多条用「；」",
        multiline: true,
      },
    ],
    build: (v) => {
      const time = s(v, "time", "HH:MM");
      const summary = s(v, "summary", "…（一句话变更摘要；多条用「；」分隔）");
      return {
        title: `约 ${time} 更新 · 请按需规划好时间 · 提前停止使用 AI 功能`,
        body: `我们将于今天约 ${time} 进行一次系统更新，预计 1–3 分钟。

更新期间 AI 功能不可用。请按需规划好时间 · 提前停止使用 AI 功能，以免进行中的对话或任务被中断。

更新完成后刷新即可；一般无需重装客户端。
本次：${summary}

若结束后仍异常，打开消息页「AgentCore 官方」或稍后重试。`,
      };
    },
  },
  {
    id: "release",
    label: "全端发版",
    description: "桌面/手机发版 · 横幅 + IM",
    title: "约 HH:MM 发版 · 请按需规划好时间 · 提前停止使用 AI 功能",
    body: `新版本将于今天约 HH:MM 起陆续上线。

更新期间 AI 功能不可用。请按需规划好时间 · 提前停止使用 AI 功能，以免进行中的对话或任务被中断。

升级方式：
· 桌面：应用内检查更新，或到官网重新下载安装
· 手机 / Web：刷新页面，或按官网指引安装新包

本次亮点：
1. …
2. …
3. …
4. …
5. …

完成后按上面方式升级即可继续使用。`,
    severity: "high",
    surface: "both",
    dismiss_policy: "once",
    card_template: "service",
    cta_label: RELEASE_CHECK_UPDATE_CTA.cta_label,
    cta_url: RELEASE_CHECK_UPDATE_CTA.cta_url,
    slots: [
      { key: "version", label: "版本号（正文可选）", placeholder: "如 0.4.2，可留空" },
      { key: "time", label: "上线时间", placeholder: "如 14:30" },
      {
        key: "highlights",
        label: "亮点（每行一条，≤5）",
        placeholder: "每行一条用户能感知的变化",
        multiline: true,
      },
    ],
    build: (v) => {
      const version = v.version?.trim() ?? "";
      const time = s(v, "time", "HH:MM");
      const lines = (v.highlights ?? "")
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean)
        .slice(0, 5);
      const highlights =
        lines.length > 0
          ? lines.map((line, i) => `${i + 1}. ${line}`).join("\n")
          : "1. …\n2. …\n3. …\n4. …\n5. …";
      const versionLine = version ? `（桌面 ${version}）` : "";
      return {
        title: `约 ${time} 发版 · 请按需规划好时间 · 提前停止使用 AI 功能`,
        body: `新版本${versionLine}将于今天约 ${time} 起陆续上线。

更新期间 AI 功能不可用。请按需规划好时间 · 提前停止使用 AI 功能，以免进行中的对话或任务被中断。

升级方式：
· 桌面：应用内检查更新，或到官网重新下载安装
· 手机 / Web：刷新页面，或按官网指引安装新包

本次亮点：
${highlights}

完成后按上面方式升级即可继续使用。`,
      };
    },
  },
  {
    id: "maintenance",
    label: "计划维护",
    description: "较长中断 · 横幅常驻至结束",
    title: "HH:MM–HH:MM 维护 · 请按需规划好时间 · 提前停止使用 AI 功能",
    body: `计划维护：今天 开始–结束（约 N 分钟）。

维护期间无法登录或发送消息，AI 功能不可用。请按需规划好时间 · 提前停止使用 AI 功能。

窗口结束后自动恢复，无需重装。
原因：…（如「数据库迁移 / 证书轮换」）`,
    severity: "critical",
    surface: "both",
    dismiss_policy: "never",
    card_template: "service",
    endHint: "建议把结束时间设为窗口结束后约 30 分钟",
    slots: [
      { key: "start", label: "开始", placeholder: "如 02:00" },
      { key: "end", label: "结束", placeholder: "如 02:40" },
      { key: "minutes", label: "约多少分钟", placeholder: "如 40" },
      {
        key: "reason",
        label: "原因",
        placeholder: "如「数据库迁移 / 证书轮换」",
      },
    ],
    build: (v) => {
      const start = s(v, "start", "HH:MM");
      const end = s(v, "end", "HH:MM");
      const minutes = s(v, "minutes", "N");
      const reason = s(v, "reason", "…（如「数据库迁移 / 证书轮换」）");
      return {
        title: `${start}–${end} 维护 · 请按需规划好时间 · 提前停止使用 AI 功能`,
        body: `计划维护：今天 ${start}–${end}（约 ${minutes} 分钟）。

维护期间无法登录或发送消息，AI 功能不可用。请按需规划好时间 · 提前停止使用 AI 功能。

窗口结束后自动恢复，无需重装。
原因：${reason}`,
      };
    },
  },
  {
    id: "policy",
    label: "政策 / 额度",
    description: "必读弹窗 · 登录后确认一次",
    title: "…政策或额度主题 · 生效 即日起",
    body: `…（一句话说明变更是什么、对用户有何影响）

生效：即日起 / 日期
详情：…（可选）
如有疑问，打开消息页「AgentCore 官方」查看本条归档。`,
    severity: "high",
    surface: "modal",
    dismiss_policy: "once",
    card_template: "service",
    slots: [
      { key: "topic", label: "主题", placeholder: "如「免费额度调整」" },
      { key: "effective", label: "生效", placeholder: "即日起 / 2026-08-10" },
      {
        key: "impact",
        label: "影响说明",
        placeholder: "变更是什么、对用户有何影响",
        multiline: true,
      },
      { key: "detail", label: "详情（可选）", placeholder: "补充说明或可留空" },
    ],
    build: (v) => {
      const topic = s(v, "topic", "…政策或额度主题");
      const effective = s(v, "effective", "即日起");
      const impact = s(v, "impact", "…（一句话说明变更是什么、对用户有何影响）");
      const detail = v.detail?.trim();
      const detailLine = detail ? `详情：${detail}\n` : "";
      return {
        title: `${topic} · 生效 ${effective}`,
        body: `${impact}

生效：${effective}
${detailLine}如有疑问，打开消息页「AgentCore 官方」查看本条归档。`,
      };
    },
  },
  {
    id: "quota_unavailable",
    label: "额度不可用 · 接入 Key",
    description: "平台额度暂不可用 · 引导接入自己的 Key",
    title: "平台额度暂时不可用 · 请接入自己的 Key",
    body: `平台提供的额度暂时不可用。

请到「设置 · 服务商」接入自己的 Key 即可继续使用。

如有疑问，打开消息页「AgentCore 官方」查看本条归档。`,
    severity: "high",
    surface: "both",
    dismiss_policy: "once",
    card_template: "service",
    endHint: "平台额度恢复后归档，或设结束时间避免过期横幅残留",
    slots: [
      {
        key: "note",
        label: "补充说明（可选）",
        placeholder: "可留空；如恢复预估时间",
        multiline: true,
      },
    ],
    build: (v) => {
      const note = v.note?.trim();
      const noteBlock = note ? `\n补充：${note}\n` : "\n";
      return {
        title: "平台额度暂时不可用 · 请接入自己的 Key",
        body: `平台提供的额度暂时不可用。

请到「设置 · 服务商」接入自己的 Key 即可继续使用。
${noteBlock}如有疑问，打开消息页「AgentCore 官方」查看本条归档。`,
      };
    },
  },
  {
    id: "quota_platform_restored",
    label: "额度已恢复 · Flash",
    description: "平台额度恢复 · 单模型 Flash（OpenCode Go）",
    title: "平台额度已恢复 · 当前仅 DeepSeek V4 Flash",
    body: `平台测试额度已恢复（内测期提供测试额度）。

当前平台模型：DeepSeek V4 Flash（经 OpenCode Go 订阅上游，非免费档。该路上 DeepSeek 按 OpenCode Go 当期 zero-retention / 零留存条款，声明写到 2026-08-31 且按月续约，非永久承诺）。额度仍为每月 / 每日上限；用尽后可在「设置 · 服务商」接入自己的 Key 继续使用。

如有疑问，打开消息页「AgentCore 官方」查看本条归档。`,
    severity: "normal",
    surface: "both",
    dismiss_policy: "once",
    card_template: "service",
    endHint: "发前先归档进行中的 quota_unavailable",
    slots: [
      {
        key: "note",
        label: "补充说明（可选）",
        placeholder: "可留空；如额度数字微调说明",
        multiline: true,
      },
    ],
    build: (v) => {
      const note = v.note?.trim();
      const noteBlock = note ? `\n补充：${note}\n` : "\n";
      return {
        title: "平台额度已恢复 · 当前仅 DeepSeek V4 Flash",
        body: `平台测试额度已恢复（内测期提供测试额度）。

当前平台模型：DeepSeek V4 Flash（经 OpenCode Go 订阅上游，非免费档。该路上 DeepSeek 按 OpenCode Go 当期 zero-retention / 零留存条款，声明写到 2026-08-31 且按月续约，非永久承诺）。额度仍为每月 / 每日上限；用尽后可在「设置 · 服务商」接入自己的 Key 继续使用。
${noteBlock}如有疑问，打开消息页「AgentCore 官方」查看本条归档。`,
      };
    },
  },
  {
    id: "outage",
    label: "故障 / 降级",
    description: "突发不可用 · 横幅常驻",
    title: "服务异常 · 处理中",
    body: `我们已察觉到 …（功能/范围）异常，正在紧急处理。

当前影响：…（登录 / 对话 / 消息等）
临时建议：稍后再试；已打开的对话可先保存草稿。
进展会同步到消息页「AgentCore 官方」。`,
    severity: "critical",
    surface: "both",
    dismiss_policy: "never",
    card_template: "service",
    endHint: "恢复后归档，或设结束时间避免过期横幅残留",
    slots: [
      { key: "scope", label: "异常范围", placeholder: "如「消息发送」" },
      {
        key: "impact",
        label: "当前影响",
        placeholder: "如「部分用户无法登录 / 对话卡住」",
        multiline: true,
      },
    ],
    build: (v) => {
      const scope = s(v, "scope", "…（功能/范围）");
      const impact = s(v, "impact", "…（登录 / 对话 / 消息等）");
      return {
        title: `服务异常 · ${scope === "…（功能/范围）" ? "处理中" : scope}`,
        body: `我们已察觉到 ${scope} 异常，正在紧急处理。

当前影响：${impact}
临时建议：稍后再试；已打开的对话可先保存草稿。
进展会同步到消息页「AgentCore 官方」。`,
      };
    },
  },
  {
    id: "feature",
    label: "功能上线",
    description: "小功能/体验改进 · 可仅 IM",
    title: "新功能 · …",
    body: `…（功能名）现已上线。

你可以：…（1–2 句怎么用 / 入口在哪）
如有反馈，请通过设置 → 反馈告诉我们。`,
    severity: "normal",
    surface: "inbox",
    dismiss_policy: "once",
    card_template: "service",
    slots: [
      { key: "name", label: "功能名", placeholder: "如「消息编辑」" },
      {
        key: "howto",
        label: "怎么用 / 入口",
        placeholder: "1–2 句",
        multiline: true,
      },
    ],
    build: (v) => {
      const name = s(v, "name", "…");
      const howto = s(v, "howto", "…（1–2 句怎么用 / 入口在哪）");
      return {
        title: `新功能 · ${name}`,
        body: `${name === "…" ? "…（功能名）" : name}现已上线。

你可以：${howto}
如有反馈，请通过设置 → 反馈告诉我们。`,
      };
    },
  },
  {
    id: "security",
    label: "安全提醒",
    description: "密码/异常登录 · 必读弹窗",
    title: "安全提醒 · …",
    body: `…（发生了什么：如「我们已为你重置密码 / 检测到异常登录」）

请你：…（改密 / 核对设备 / 忽略说明）
如非本人操作，打开消息页「AgentCore 官方」或通过设置 → 反馈联系我们。`,
    severity: "high",
    surface: "modal",
    dismiss_policy: "once",
    card_template: "service",
    slots: [
      { key: "topic", label: "主题", placeholder: "如「密码已重置」" },
      {
        key: "what",
        label: "发生了什么",
        placeholder: "如「我们已为你重置密码」",
        multiline: true,
      },
      {
        key: "action",
        label: "请用户做什么",
        placeholder: "如「登录后立即修改密码并核对设备」",
        multiline: true,
      },
    ],
    build: (v) => {
      const topic = s(v, "topic", "…");
      const what = s(
        v,
        "what",
        "…（发生了什么：如「我们已为你重置密码 / 检测到异常登录」）",
      );
      const action = s(v, "action", "…（改密 / 核对设备 / 忽略说明）");
      return {
        title: `安全提醒 · ${topic}`,
        body: `${what}

请你：${action}
如非本人操作，打开消息页「AgentCore 官方」或通过设置 → 反馈联系我们。`,
      };
    },
  },
  {
    id: "campaign",
    label: "内测 / 活动",
    description: "征集反馈或限时活动 · IM",
    title: "邀请 · …",
    body: `…（活动/内测一句话）

参与方式：…（入口或步骤）
时间：…（起止或「即日起」）
欢迎把体验反馈发到设置 → 反馈。`,
    severity: "normal",
    surface: "inbox",
    dismiss_policy: "once",
    card_template: "service",
    slots: [
      { key: "name", label: "活动名", placeholder: "如「协作图内测」" },
      {
        key: "blurb",
        label: "一句话介绍",
        placeholder: "活动/内测是什么",
        multiline: true,
      },
      {
        key: "howto",
        label: "参与方式",
        placeholder: "入口或步骤",
        multiline: true,
      },
      { key: "when", label: "时间", placeholder: "即日起 / 8.1–8.15" },
    ],
    build: (v) => {
      const name = s(v, "name", "…");
      const blurb = s(v, "blurb", "…（活动/内测一句话）");
      const howto = s(v, "howto", "…（入口或步骤）");
      const when = s(v, "when", "…（起止或「即日起」）");
      return {
        title: `邀请 · ${name}`,
        body: `${blurb}

参与方式：${howto}
时间：${when}
欢迎把体验反馈发到设置 → 反馈。`,
      };
    },
  },
  {
    id: "article",
    label: "功能故事 · 图文",
    description: "长内容图文卡 · 须填摘要 · 可选封面",
    title: "…（故事标题）",
    body: `…（完整正文；用户点「阅读全文」后在应用内详情阅读）

你可以：…（可选入口或下一步）
欢迎把体验反馈发到设置 → 反馈。`,
    summary: "…（卡面摘要，两句内）",
    severity: "normal",
    surface: "inbox",
    dismiss_policy: "once",
    card_template: "article",
    slots: [
      { key: "title", label: "标题", placeholder: "如「协作图怎么用」" },
      {
        key: "summary",
        label: "摘要（卡面必填）",
        placeholder: "两句内，会话矮卡展示",
        multiline: true,
      },
      {
        key: "body",
        label: "正文",
        placeholder: "完整说明，详情页展示",
        multiline: true,
      },
    ],
    build: (v) => {
      const title = s(v, "title", "…（故事标题）");
      const summary = s(v, "summary", "…（卡面摘要，两句内）");
      const body = s(
        v,
        "body",
        `…（完整正文；用户点「阅读全文」后在应用内详情阅读）

你可以：…（可选入口或下一步）
欢迎把体验反馈发到设置 → 反馈。`,
      );
      return { title, body, summary };
    },
  },
  {
    id: "changelog",
    label: "Changelog · 图文",
    description: "版本亮点长文 · 须填摘要 · 可选封面",
    title: "版本亮点 · …",
    body: `本版带来这些用户可感知的变化：

1. …
2. …
3. …

升级方式：桌面检查更新，或到官网重新下载；手机 / Web 刷新即可。`,
    summary: "…（本版一句话亮点）",
    severity: "normal",
    surface: "inbox",
    dismiss_policy: "once",
    card_template: "article",
    cta_label: RELEASE_CHECK_UPDATE_CTA.cta_label,
    cta_url: RELEASE_CHECK_UPDATE_CTA.cta_url,
    slots: [
      { key: "version", label: "版本号（可选）", placeholder: "如 0.4.2" },
      {
        key: "summary",
        label: "摘要（卡面必填）",
        placeholder: "一句话概括本版亮点",
        multiline: true,
      },
      {
        key: "highlights",
        label: "亮点（每行一条）",
        placeholder: "用户能感知的变化",
        multiline: true,
      },
    ],
    build: (v) => {
      const version = v.version?.trim() ?? "";
      const summary = s(v, "summary", "…（本版一句话亮点）");
      const lines = (v.highlights ?? "")
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
      const highlights =
        lines.length > 0
          ? lines.map((line, i) => `${i + 1}. ${line}`).join("\n")
          : "1. …\n2. …\n3. …";
      const title = version
        ? `版本亮点 · ${version}`
        : "版本亮点 · …";
      return {
        title,
        summary,
        body: `本版带来这些用户可感知的变化：

${highlights}

升级方式：桌面检查更新，或到官网重新下载；手机 / Web 刷新即可。`,
      };
    },
  },
];

export type NoticeFormSeed = {
  title: string;
  body: string;
  severity: NoticeTemplateSeverity;
  surface: NoticeTemplateSurface;
  dismiss_policy: NoticeTemplateDismiss;
  card_template: NoticeCardTemplate;
  summary: string;
  cover_url: string;
  cta_label: string;
  cta_url: string;
  start_at: string;
  end_at: string;
};

export function emptySlotValues(t: NoticeTemplate): Record<string, string> {
  const out: Record<string, string> = {};
  for (const slot of t.slots) out[slot.key] = "";
  return out;
}

export function templateToFormSeed(t: NoticeTemplate): NoticeFormSeed {
  return {
    title: t.title,
    body: t.body,
    severity: t.severity,
    surface: t.surface,
    dismiss_policy: t.dismiss_policy,
    card_template: t.card_template ?? "service",
    summary: t.summary ?? "",
    cover_url: "",
    cta_label: t.cta_label ?? "",
    cta_url: t.cta_url ?? "",
    start_at: "",
    end_at: "",
  };
}

/** 用槽位值生成标题/正文（及 article 摘要）；空槽位保留骨架占位。 */
export function buildFromSlots(
  t: NoticeTemplate,
  values: Record<string, string>,
): NoticeTemplateBuild {
  return t.build(values);
}

/** 发布前给人看的展示面说明（不替代真实校验）。 */
export function surfacePublishHint(
  surface: NoticeTemplateSurface,
  dismiss: NoticeTemplateDismiss,
): string {
  const parts: string[] = [];
  if (surface === "banner" || surface === "both") {
    parts.push("桌面顶栏横幅");
  }
  if (surface === "modal") {
    parts.push("登录后一次性弹窗");
  }
  if (surface === "inbox" || surface === "both" || surface === "modal") {
    parts.push("IM「AgentCore 官方」一条共享卡片");
  }
  if (surface === "modal" && dismiss === "never") {
    return "弹窗仅支持「可关闭」策略，请改为 once 后再发布";
  }
  if (dismiss === "never" && (surface === "banner" || surface === "both")) {
    parts.push("关闭策略为常驻：横幅可关但仍可能回潮至结束/归档");
  }
  return parts.length > 0 ? `发布后将出现在：${parts.join(" · ")}` : "";
}
