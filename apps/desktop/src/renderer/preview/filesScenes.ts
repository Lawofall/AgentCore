import { type FileNode, type FileSource, parentDir } from "@/lib/fileSource";
import type { DocumentNode } from "@/services/documents";

/** Offline mock scenes for `#/preview/files` — empty vs populated entries. */
export const FILES_PREVIEW_SCENES = [
  {
    id: "files-empty",
    title: "空态",
    description: "新账号 · 还没有自建条目",
  },
  {
    id: "files-entries",
    title: "有条目",
    description: "文件夹条目",
  },
] as const;

export type FilesPreviewSceneId = (typeof FILES_PREVIEW_SCENES)[number]["id"];

export const FILES_PREVIEW_PROJECT_FOLDER_ID = "folder-demo";

/**
 * 文件夹自己的盘上文件——预览里跟条目同屏：左栏只有文件夹树；
 * 文件夹条目经 ``renderWorkroomLead`` 进 ``.agentcore``（盘上 ``AgentCore/``），钉在同级最前。
 * 账号提示词在工具箱；文件页只挂文件夹 ``.agentcore``。
 */
const PREVIEW_WORKSPACE_TREE: FileNode[] = [
  { path: "合同", name: "合同", isDir: true },
  { path: "合同/服务协议.md", name: "服务协议.md", isDir: false },
  { path: "报告.md", name: "报告.md", isDir: false },
  { path: "AgentCore", name: "AgentCore", isDir: true },
  { path: "AgentCore/文档", name: "文档", isDir: true },
  { path: "AgentCore/文档/工作稿", name: "工作稿", isDir: true },
  { path: "AgentCore/文档/工作稿/初稿.md", name: "初稿.md", isDir: false },
];

const previewReadOnly = () => Promise.reject(new Error("预览为只读桩"));

/**
 * Offline stub source for the preview's file tree — listing only, no mutations.
 * Its `id` must not look like a scene id: `shoot-files.mjs` scrapes this file for
 * scene ids by pattern, and a match here would shoot a scene that does not exist.
 */
export const filesPreviewSource: FileSource = {
  id: "preview-workspace",
  label: "示例文件夹",
  caps: { watch: false, transfer: false, edit: false, snapshots: false },
  listDir: (dir: string) =>
    Promise.resolve(
      PREVIEW_WORKSPACE_TREE.filter((n) => parentDir(n.path) === dir),
    ),
  listTree: () => Promise.resolve([...PREVIEW_WORKSPACE_TREE]),
  read: previewReadOnly,
  createFile: previewReadOnly,
  mkdir: previewReadOnly,
  move: previewReadOnly,
  delete: previewReadOnly,
};

export function buildGlobalEntriesMock(): DocumentNode[] {
  return [
    {
      id: "g-pref",
      parentId: null,
      folderId: null,
      kind: "document",
      role: "rule",
      aiMaintained: true,
      applyMode: "always",
      description: "沟通与工作习惯",
      name: "偏好.md",
      frontmatterError: null,
      disputedAt: null,
      alwaysChars: null,
    },
    {
      id: "g-profile",
      parentId: null,
      folderId: null,
      kind: "document",
      role: "rule",
      aiMaintained: true,
      applyMode: "always",
      description: "用户长期事实",
      name: "画像.md",
      frontmatterError: null,
      disputedAt: null,
      alwaysChars: null,
    },
    {
      id: "g-rule",
      parentId: null,
      folderId: null,
      kind: "document",
      role: "rule",
      aiMaintained: false,
      applyMode: "always",
      description: "回复语气与禁忌",
      name: "语气.md",
      frontmatterError: null,
      disputedAt: null,
      alwaysChars: 2200,
    },
    {
      id: "g-ondemand",
      parentId: null,
      folderId: null,
      kind: "document",
      role: "rule",
      aiMaintained: false,
      applyMode: "on_demand",
      description: "偶发合规附录",
      name: "合规附录.md",
      frontmatterError: null,
      disputedAt: null,
      alwaysChars: null,
    },
    {
      id: "g-bad",
      parentId: null,
      folderId: null,
      kind: "document",
      role: "rule",
      aiMaintained: false,
      applyMode: "on_demand",
      description: "",
      name: "坏条目.md",
      frontmatterError: "unclosed frontmatter",
      disputedAt: null,
      alwaysChars: null,
    },
    {
      id: "g-topic",
      parentId: null,
      folderId: null,
      kind: "document",
      role: "rule",
      aiMaintained: true,
      applyMode: "on_demand",
      description: "长文写作备忘",
      name: "主题/写作.md",
      frontmatterError: null,
      disputedAt: null,
      alwaysChars: null,
    },
  ];
}

/** New-account / empty cores — real rows with 0 always chars. */
export function buildEmptyGlobalEntriesMock(): DocumentNode[] {
  return [];
}

export function buildProjectEntriesMock(folderId: string): DocumentNode[] {
  return [
    {
      id: "p-profile",
      parentId: null,
      folderId,
      kind: "document",
      role: "rule",
      aiMaintained: true,
      applyMode: "always",
      description: "本文件夹技术栈与事实",
      name: "画像.md",
      frontmatterError: null,
      disputedAt: null,
      alwaysChars: null,
    },
    {
      id: "p-nav",
      parentId: null,
      folderId,
      kind: "document",
      role: "rule",
      aiMaintained: true,
      applyMode: "always",
      description: "一句话定位 + 任务路由",
      name: "导航.md",
      frontmatterError: null,
      disputedAt: null,
      alwaysChars: null,
    },
    {
      id: "p-topic",
      parentId: null,
      folderId,
      kind: "document",
      role: "rule",
      aiMaintained: true,
      applyMode: "on_demand",
      description: "部署流程备忘",
      name: "主题/部署流程.md",
      frontmatterError: null,
      disputedAt: null,
      alwaysChars: null,
    },
  ];
}

export function buildEmptyProjectEntriesMock(
  _folderId: string,
): DocumentNode[] {
  return [];
}

export function entriesForScene(sceneId: FilesPreviewSceneId): {
  global: DocumentNode[];
  project: DocumentNode[];
} {
  const folderId = FILES_PREVIEW_PROJECT_FOLDER_ID;
  if (sceneId === "files-empty") {
    return {
      global: buildEmptyGlobalEntriesMock(),
      project: buildEmptyProjectEntriesMock(folderId),
    };
  }
  return {
    global: buildGlobalEntriesMock(),
    project: buildProjectEntriesMock(folderId),
  };
}
