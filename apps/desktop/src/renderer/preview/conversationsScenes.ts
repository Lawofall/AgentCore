import type { GroupedConversations } from "@/hooks/useConversations";
import type { ConversationTrash } from "@/services/conversations";
import type { FolderMeta, FolderTrash } from "@/services/folders";
import type { Conversation } from "@/stores/conversation";

function hoursAgo(h: number): string {
  return new Date(Date.now() - h * 3_600_000).toISOString();
}

function daysAgo(d: number): string {
  return new Date(Date.now() - d * 86_400_000).toISOString();
}

function daysAhead(d: number): string {
  return new Date(Date.now() + d * 86_400_000).toISOString();
}

const MOCK_FOLDERS: FolderMeta[] = [
  {
    id: "folder-product",
    name: "产品设计",
    mode: "cloud",
    localRootId: null,
    localSubpath: null,
  },
  {
    id: "folder-eng",
    name: "工程落地",
    mode: "local",
    localRootId: "root-1",
    localSubpath: null,
  },
  {
    id: "folder-research",
    name: "调研笔记",
    mode: "cloud",
    localRootId: null,
    localSubpath: null,
  },
];

const MOCK_CONVERSATIONS: Conversation[] = [
  {
    id: "c-pin-1",
    title: "Q3 路线图讨论",
    updatedAt: hoursAgo(2),
    messageCount: 42,
    lastMessagePreview: "好的，下周一把竞品对标补进附录。",
    folderId: "folder-product",
    pinned: true,
  },
  {
    id: "c-today-1",
    title: "桌面端对话列表改版",
    updatedAt: hoursAgo(1),
    messageCount: 18,
    lastMessagePreview: "时间线分组 + 信息密度是这轮的主轴。",
    folderId: "folder-eng",
  },
  {
    id: "c-today-2",
    title: "审批流文案校对",
    updatedAt: hoursAgo(4),
    messageCount: 7,
    lastMessagePreview: "「等你决策」比「待处理」更贴近产品语气。",
    folderId: "folder-product",
  },
  {
    id: "c-yest-1",
    title: "Agent 身份色板对齐",
    updatedAt: daysAgo(1),
    messageCount: 11,
    lastMessagePreview: "文件夹圆点复用 --agent-N，避免硬编码 hex。",
    folderId: "folder-eng",
  },
  {
    id: "c-yest-2",
    title: "未分组随想",
    updatedAt: new Date(Date.now() - 86_400_000 - 3_600_000).toISOString(),
    messageCount: 3,
    lastMessagePreview: "先记一下，回头再归进文件夹。",
    folderId: null,
  },
  {
    id: "c-week-1",
    title: "竞品历史会话体验",
    updatedAt: daysAgo(3),
    messageCount: 29,
    lastMessagePreview: "Linear / Superhuman 的密度值得借鉴。",
    folderId: "folder-research",
  },
  {
    id: "c-week-2",
    title: "批量归档交互",
    updatedAt: daysAgo(5),
    messageCount: 14,
    lastMessagePreview: "sticky 操作条保留，选择态降级为图标。",
    folderId: "folder-eng",
  },
  {
    id: "c-earlier-1",
    title: "首版侧栏对话行",
    updatedAt: daysAgo(20),
    messageCount: 56,
    lastMessagePreview: "侧栏保持紧凑；管理页另起一套行组件。",
    folderId: "folder-eng",
  },
  {
    id: "c-earlier-2",
    title: "用户访谈纪要 · 五月",
    updatedAt: daysAgo(45),
    messageCount: 8,
    lastMessagePreview: "「一眼看不到文件夹归属」是高频抱怨。",
    folderId: "folder-research",
  },
];

const MOCK_ARCHIVED: Conversation[] = [
  {
    id: "c-arch-1",
    title: "旧版布局草案",
    updatedAt: daysAgo(60),
    messageCount: 22,
    lastMessagePreview: "左右分栏纯文字列表，信息密度不足。",
    folderId: "folder-product",
    archived: true,
  },
  {
    id: "c-arch-2",
    title: "一次性实验对话",
    updatedAt: daysAgo(90),
    messageCount: 2,
    lastMessagePreview: "这条线索没走通，先归档。",
    folderId: null,
    archived: true,
  },
];

const MOCK_TRASH: FolderTrash = {
  retentionDays: 30,
  items: [
    {
      id: "folder-gone-cloud",
      name: "旧版官网改版",
      mode: "cloud",
      deletedAt: hoursAgo(3),
      purgeAt: daysAhead(29),
    },
    {
      id: "folder-gone-local",
      name: "本机实验仓",
      mode: "local",
      deletedAt: daysAgo(28),
      purgeAt: daysAhead(2),
    },
    {
      id: "folder-gone-soon",
      name: "临时素材",
      mode: "cloud",
      deletedAt: daysAgo(30),
      purgeAt: new Date(Date.now() + 5 * 3_600_000).toISOString(),
    },
  ],
};

const MOCK_CONVERSATION_TRASH: ConversationTrash = {
  retentionDays: 30,
  items: [
    {
      id: "c-gone-1",
      title: "误删的定价讨论",
      folderId: "folder-product",
      messageCount: 24,
      deletedAt: hoursAgo(1),
      purgeAt: daysAhead(30),
    },
    {
      // 原文件夹也在回收站里 → 行上要说清「恢复后先回到快速对话」。
      id: "c-gone-2",
      title: "官网改版对齐",
      folderId: "folder-gone-cloud",
      messageCount: 7,
      deletedAt: daysAgo(2),
      purgeAt: daysAhead(28),
    },
    {
      id: "c-gone-3",
      title: "随手记的想法",
      folderId: null,
      messageCount: 3,
      deletedAt: daysAgo(29),
      purgeAt: new Date(Date.now() + 8 * 3_600_000).toISOString(),
    },
  ],
};

export type ConversationsPreviewScene = {
  id: string;
  title: string;
  description: string;
};

export const CONVERSATIONS_PREVIEW_SCENES: readonly ConversationsPreviewScene[] =
  [
    {
      id: "conversations-timeline",
      title: "时间线列表",
      description: "置顶 / 今天 / 昨天 / 本周 / 更早 · 亮色",
    },
    {
      id: "conversations-archived",
      title: "已归档视图",
      description: "归档行与时间线同密度",
    },
    {
      id: "conversations-collaboration",
      title: "文件夹协作时间线",
      description: "文件夹筛选 · 幕摘要 + 阶段产物",
    },
    {
      id: "conversations-trash",
      title: "最近删除",
      description: "已删对话 + 已删文件夹 · 保留期倒计时 + 恢复",
    },
  ] as const;

/** Offline mock for `#/preview/conversations?s=conversations-collaboration`. */
export function buildCollaborationTimelineMock(folderId: string) {
  return {
    folder_id: folderId,
    total: 2,
    limit: 20,
    offset: 0,
    dossier_refs_note:
      "路径级约定文档消费事实（本场辩论开赛注入或会话内 file_read），非跨会话过程边",
    items: [
      {
        conversation_id: "c-pin-1",
        title: "Q3 路线图讨论",
        updated_at: hoursAgo(2),
        execution_id: "exec-1",
        host_turn_id: "turn-mlr-1",
        acts: [
          {
            act_id: "act-1",
            kind: "multi_agent" as const,
            title: "多视角调研",
          },
          {
            act_id: "act-2",
            kind: "debate" as const,
            title: "辩论对抗",
          },
        ],
        dossier_refs: [
          {
            path: "AgentCore/文档/research/法律透镜报告.md",
            sources: ["dossier_inject", "file_read"] as (
              | "dossier_inject"
              | "file_read"
            )[],
          },
          {
            path: "AgentCore/文档/research/汇总与命题卡.md",
            sources: ["file_read"] as ("dossier_inject" | "file_read")[],
          },
        ],
      },
      {
        conversation_id: "c-today-2",
        title: "审批流文案校对",
        updated_at: hoursAgo(4),
        execution_id: "exec-2",
        host_turn_id: "turn-debate-1",
        acts: [
          {
            act_id: "act-1",
            kind: "debate" as const,
            title: "辩论对抗",
          },
        ],
        dossier_refs: [],
      },
    ],
  };
}

export function buildConversationsPreviewGrouped(): GroupedConversations {
  return {
    folders: MOCK_FOLDERS,
    conversations: MOCK_CONVERSATIONS,
  };
}

export function buildConversationsPreviewArchived(): Conversation[] {
  return MOCK_ARCHIVED;
}

export function buildConversationsPreviewTrash(): FolderTrash {
  return MOCK_TRASH;
}

export function buildConversationsPreviewConversationTrash(): ConversationTrash {
  return MOCK_CONVERSATION_TRASH;
}
