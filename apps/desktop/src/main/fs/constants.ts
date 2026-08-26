export const TEXT_PREVIEW_CAP = 256 * 1024; // 文本预览最多读取/展示 256KB
export const IMAGE_PREVIEW_CAP = 10 * 1024 * 1024; // 图片超过 10MB 退化为元信息
/** PDF 面板内预览上限：略高于图帽（15 MiB），常见文档可 iframe 内嵌；更大请下载/系统打开。 */
export const PDF_PREVIEW_CAP = 15 * 1024 * 1024;
export const EDIT_READ_MAX = 5 * 1024 * 1024; // 「读以编辑」整文入内存上限 5 MiB（超出不在面板内编辑）

export const LIST_FILES_CAP = 5000; // @ 提及检索：单根最多返回文件数
export const LIST_FILES_MAX_DEPTH = 12; // 递归最大深度，防极深目录
// 递归列举跳过集：权威定义在 workspaceIgnore.ts（与服务端 IGNORED_DIRS 对齐）。
export { LIST_FILES_SKIP_DIRS } from "./workspaceIgnore";

// --- 本地工作区 op（双模式工作区 P2）执行边界 ---
// 整文文本读取上限：云 ServerWorkspace 与桌面 Local 对齐 ``WORKSPACE_READ_MAX``（5 MiB）。
// Office/PDF 过桥抽取摄入用 ``WORKSPACE_EXTRACT_SOURCE_MAX``（25 MiB IPC）。
// 磁盘后端抽取顶是服务端 ``OFFICE_EXTRACT_DISK_MAX_BYTES``（100 MiB）；上传顶另见 50 MiB。
export const WORKSPACE_READ_MAX = 5 * 1024 * 1024; // 5 MiB
/** Magic sniff / OLE size — mirrors server ``WORKSPACE_READ_HEAD_MAX_BYTES``. */
export const WORKSPACE_READ_HEAD_MAX = 1024;
/** Channel extract ingest — mirrors server ``OFFICE_EXTRACT_CHANNEL_MAX_BYTES``. */
export const WORKSPACE_EXTRACT_SOURCE_MAX = 25 * 1024 * 1024; // 25 MiB
// AI 面单次列举默认上限，与 ServerWorkspace.list 的 _MAX_LIST_ENTRIES 对齐。
// 命中即在 op 结果里回 `truncated: true`——上限可以有，静默不行。
export const WORKSPACE_LIST_MAX = 100;
export const GREP_MAX_LINE = 300; // 截断超长命中行（如压缩产物），与服务端对齐
export const GREP_MAX_FILES = 5000; // 单次 grep 最多打开文件数
export const GREP_MAX_RESULTS_CAP = 200; // 结果硬上限
/** grep 单文件大小帽（与服务端 rg_grep.GREP_MAX_FILE_BYTES 对齐，2 MiB） */
export const GREP_MAX_FILE_BYTES = 2 * 1024 * 1024;

// 本地→云交接打包（双模式工作区 P2e / e1）上限：防超大仓把整树读入内存/撑爆通道回填。
export const ARCHIVE_MAX_FILES = 20000; // 最多打包文件数
export const ARCHIVE_MAX_BYTES = 100 * 1024 * 1024; // 原始字节上限（zip 前）100 MiB

// 回合基线 zip（`AgentCore/baselines/<message_id>.zip`）保留策略：一回合一份整树 zip，
// 不清理就在用户磁盘上永久堆积。数量上限 ∧ TTL，捕获落盘后顺带清理（对齐云端 D+C）。
// 主进程读不到服务端配置，只能逐值镜像 `settings.workspace_local_baseline_{max,
// retention_days}`（`apps/server/agentcore/config/workspace.py`）。用户命名版本区
// `AgentCore/versions` 永不自动清理，不在此策略内。
export const BASELINE_KEEP_MAX = 20;
export const BASELINE_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000; // 30 天

// 本地代码执行（P2c）：镜像服务端 SubprocessSandbox。命令/扩展名一一对齐；
// 进程 cwd = 绑定的本地根（让代码与文件工具同目录，呼应服务端 cwd=workspace）。
//
// 超时分工：本通道上限须能兑现 ``test_run`` 外环验收墙钟（typecheck/build 600s +
// engine slack）；``code_execute`` 工具自身仍在服务端把请求 clamp 到 ≤60s，不靠本帽
// 当工具上限。install/test 仍用较短预算，由服务端按 check 分档。
export const EXEC_LANGS: Record<string, { cmd: string[]; ext: string }> = {
  python: { cmd: ["python", "-u"], ext: ".py" },
  javascript: { cmd: ["node"], ext: ".js" },
  bash: { cmd: ["bash"], ext: ".sh" },
};
/** Workspace ``execute`` 通道墙钟上限（秒）。外环验收墙钟：≥ typecheck/build 600 + 30 slack。 */
export const EXEC_TIMEOUT_CAP_S = 1230;
// 单流捕获硬上限：防失控输出占内存/撑大通道回填；模型可见截断（8000）由服务端
// ExecutionResult.__post_init__ 统一处理，故此处留足余量、不抢那层语义。
export const EXEC_CAPTURE_CAP = 100_000;

export const IMAGE_MIME: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
  ".bmp": "image/bmp",
  ".ico": "image/x-icon",
  ".avif": "image/avif",
};
