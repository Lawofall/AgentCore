/**
 * 本地文件系统 IPC 契约 —— 主进程 / preload / renderer 三端共享的单一真相源。
 *
 * 设计约束：
 * - renderer 寻址仍以 `{ rootId, relPath }`。`listRoots` 带 `absPath` 供 @ 提及折叠嵌套根
 *   与同名消歧（本机可有两个同名根指向不同路径）；读写文件等其它 IPC 仍不回传绝对路径。
 * - 所有可能失败的操作统一返回 `FsResult`（判别结果），不向 renderer 抛异常。
 */

/** 一个已授权的本地根目录。 */
export interface FsRoot {
  id: string;
  name: string;
  /**
   * 本机绝对路径。`listRoots` 必带，供 @ 提及嵌套折叠与同名消歧。
   * 寻址仍用 `{ rootId, relPath }`，不要拿本字段去读文件。
   * 会话授权根（grant / `listSessionReadonlyRoots`）不下发。
   */
  absPath?: string;
  /** W3 session grant alias under ``external/<alias>/`` (omit for permanent roots). */
  alias?: string;
  /** Session access mode (readonly | organize | attach_rw); omit for permanent roots. */
  mode?: "readonly" | "organize" | "attach_rw";
  sessionOnly?: boolean;
}

/** 目录项（懒加载的单层子项）。 */
export interface FsEntry {
  name: string;
  /** 相对所属根目录的路径，统一用 "/" 分隔；根目录自身为 ""。 */
  relPath: string;
  kind: "file" | "dir";
  /** 文件字节数；目录为 null。 */
  size: number | null;
  /** 最近修改时间（毫秒时间戳）；不可得为 null。 */
  modifiedMs: number | null;
}

/** 文件预览结果：文本 / 图片 / PDF（data URL）/ 二进制（仅元信息）。 */
export type FilePreview =
  | { kind: "text"; content: string; truncated: boolean }
  | { kind: "image"; dataUrl: string; mime: string; size: number }
  | { kind: "pdf"; dataUrl: string; mime: string; size: number }
  | { kind: "binary"; mime: string; size: number; reason: string };

/** 扁平文件条目（用于 @ 提及检索；只含文件，不含目录）。 */
export interface FsFileRef {
  /** 相对所属根目录的路径，统一用 "/" 分隔。 */
  relPath: string;
  /** 文件名（relPath 的最后一段）。 */
  name: string;
  /**
   * 本机 mtime（毫秒）。仅 `listFiles({ order: "recent" })` 带上；
   * 默认 path 序不 stat、不填，避免 OneDrive 占位水合与空态误用 0。
   */
  mtimeMs?: number;
}

/** @ 提及扁平索引排序：字母序（默认）或按本机 mtime 倒序。 */
export type FsListFilesOrder = "path" | "recent";

/**
 * `listFiles` 成功载荷。`truncated` 表示命中单根上限（`LIST_FILES_CAP`），
 * 列表不完整——调用方必须看见，不能当「根里就这些文件」。
 */
export interface FsListFilesResult {
  files: FsFileRef[];
  truncated: boolean;
}

/** 工作区 ``AgentCore/trash`` 条目（产品一键还原；非 OS 回收站）。 */
export interface WorkspaceTrashEntry {
  entryId: string;
  originalPath: string;
  name: string;
  isDir: boolean;
  deletedAt: string;
}

/**
 * 工作区 ``AgentCore/versions`` 条目（用户命名版本 = 本地版「留版本」）。
 * 字段与云端 ``SnapshotSummary`` 一一对位（`name` ↔ `label`），只是本地按盘上
 * 目录寻址而非快照 id。
 */
export interface WorkspaceVersionEntry {
  versionId: string;
  name: string;
  createdAt: string;
  sizeBytes: number;
}

/**
 * Fs IPC 失败判别码——renderer 按码分支（如懒物化工作区对 `not_found`），
 * **禁止**匹配 `reason` 中文文案。
 */
export type FsErrorCode =
  | "not_found"
  | "out_of_root"
  | "unauthorized"
  | "invalid"
  | "exists"
  | "denied"
  | "busy"
  | "error";

/** 统一的判别式结果。 */
export type FsResult<T = void> =
  | { ok: true; data: T }
  | { ok: false; reason: string; code: FsErrorCode };

export type FsCreateKind = "file" | "dir";

/** 文本文件编码（读侧嗅探）；`gbk` 仅可读不可回写（未引入编码器）。 */
export type FsEncoding = "utf-8" | "utf-8-bom" | "gbk";
/** 换行风格——回写时按原文还原，避免整文换行 diff。 */
export type FsEol = "lf" | "crlf";

/**
 * 「读以编辑」结果：完整正文 + 写前 CAS 基线（mtime）+ 原文编码/换行。
 *
 * 与预览 `readFile` 分工：预览有 256KB 截断且判别图片/二进制；编辑必须拿到**完整**正文
 * （截断正文一旦保存会丢尾），故编辑走独立通道 `fs:readTextFile`。
 */
export interface FsTextFile {
  /** 完整正文，换行已统一为 `\n`（回写时按 `eol` 还原）。 */
  content: string;
  /** 写前 CAS 基线：保存时与磁盘 mtime 比对，不符即冲突。 */
  mtimeMs: number;
  encoding: FsEncoding;
  eol: FsEol;
}

/** 写文本文件的输入（带写前 CAS 基线）。 */
export interface FsWriteInput {
  /** 编辑器正文（`\n` 换行）；主进程按 `eol`/`encoding` 还原落盘。 */
  content: string;
  /** 来自读取基线；`gbk` 会被拒写。 */
  encoding: FsEncoding;
  eol: FsEol;
  /** 写前 CAS：与磁盘 mtime 不符即 `conflict`；`0` 视为新建。 */
  baselineMtimeMs: number;
}

/**
 * 写盘结果（判别式）。`conflict` 带磁盘当前 `mtimeMs` 供「仍然覆盖」用其做基线再写；
 * 其余失败原因：`denied`（越权/未授权）/`locked`（被占用）/`unsupported`（GBK 回写未启用）/`error`。
 */
export type FsWriteResult =
  | { ok: true; mtimeMs: number }
  | { ok: false; reason: "conflict"; diskMtimeMs: number }
  | {
      ok: false;
      reason: "denied" | "locked" | "unsupported" | "error";
      message?: string;
    };

/**
 * 本地工作区 op 名（双模式工作区）—— 与服务端 ``WorkspaceOp`` 一一对应。
 *
 * 服务端 ``LocalWorkspace`` 把每个 backend 方法序列化成一条 op 经 SSE 下发，主进程
 * 在授权根上执行后回填。覆盖读 / 写 / 树 / grep / 执行 / 进程管理等全套 op（不再仅
 * P2a 只读三件套）。
 *
 * ``archive`` 不对应任何 backend 方法——它是本地→云交接（P2e / e1）专用 op：把整个绑定
 * 根打包成单个归档（套用忽略规则）交服务端暂存并快照，由 handoff 编排直接下发。
 * ``ensure_turn_baseline`` 同样不是 backend 方法——桌面通道 Local 回合 zip 基线
 *（``AgentCore/baselines/{message_id}.zip``）：探测非空 zip，缺则落盘；服务端无用户盘
 * Path.root，破坏形闸问 ready 而非 backend 有无 Path。
 * ``probe_exec`` 同样不是 backend 方法——回合准备时探测本机 code_execute 可用解释器，
 * 供服务端裁剪工具 schema（坏 WSL bash 等不进 enum）。
 * ``diagnostics`` 同样不是 backend 方法——本地 TypeScript LanguageService 诊断（写码验证内环）；
 * 云端无 LS 时诚实 ``status=unavailable``，不把通道打挂。
 * ``git_repo_status`` / ``git_scm`` 同样不是 backend 方法——桌面 U1–U3 用户 SCM
 *（只读摘要 + stage/commit/push/pull）；渲染层经 ``workspaceOp`` 直调，服务端/Agent 不发此 op。
 * ``git_run`` 同样不是 backend 方法——Agent 结构化 ``git`` 在 LocalWorkspace 上经通道
 * 本机执行 allowlisted argv（``cwd`` = 项目 subpath 时落子目录，与 file_* 同基准；
 * 无 subpath 则绑定根即项目）；与 UI SCM 分立。
 */
export type WorkspaceOpName =
  | "read"
  | "write"
  | "append"
  | "read_bytes"
  | "read_head"
  | "write_bytes"
  | "list"
  | "exists"
  | "read_lines"
  | "list_tree"
  | "index_files"
  | "mkdir"
  | "delete"
  | "copy"
  | "move"
  | "replace"
  | "grep"
  | "execute"
  | "probe_exec"
  | "archive"
  | "ensure_turn_baseline"
  | "process_start"
  | "process_read"
  | "process_stop"
  | "process_list"
  | "diagnostics"
  | "git_repo_status"
  | "git_scm"
  | "git_run";

/** ``git_run`` 成功 value —— Agent 结构化 git 通道回填。 */
export interface GitRunValue {
  stdout: string;
  stderr: string;
  exit_code: number;
}

/** U2：单条 Git 变更（path + porcelain XY 片段）。 */
export interface GitChangeEntry {
  path: string;
  /** 如 ``M `` / `` M`` / ``??`` */
  code: string;
}

/**
 * ``git_repo_status`` 成功 value —— U1 chip + U2 变更列表。
 * ``present:false`` = 无仓 / git 不可用（UI 不挂 chip / git 轨，勿假成功）。
 */
export type GitRepoStatusValue =
  | { present: false }
  | {
      present: true;
      branch: string;
      dirty: boolean;
      ahead?: number;
      behind?: number;
      staged?: GitChangeEntry[];
      unstaged?: GitChangeEntry[];
      conflicted?: string[];
    };

/**
 * 一次本地 op 的执行结果信封 —— 形状与服务端回填端点 `ResolveClientToolInteraction.result`
 * 对齐：成功带 `value`（op 相关）；失败带类型化 `error`，其 `kind` 直接映射回服务端
 * 的 `WorkspaceError` 子类（如 `PathNotFound`），从而工具层报错文案与云模式一致。
 */
export type WorkspaceOpResult =
  | { ok: true; value: unknown }
  | { ok: false; error: { kind: string; detail: string; count?: number } };

/** 主进程 → renderer 的目录变更事件（watch 命中后发出）。 */
export interface FsChangedEvent {
  rootId: string;
  relPath: string;
}

/** IPC 通道名 —— 主进程与 preload 共用，避免硬编码漂移。 */
export const FS_CHANNELS = {
  addRoot: "fs:addRoot",
  ensureDefaultRoot: "fs:ensureDefaultRoot",
  listRoots: "fs:listRoots",
  removeRoot: "fs:removeRoot",
  /** W3: session-scoped read-only root for one conversation. */
  grantSessionReadonlyRoot: "fs:grantSessionReadonlyRoot",
  listSessionReadonlyRoots: "fs:listSessionReadonlyRoots",
  revokeSessionReadonlyRoot: "fs:revokeSessionReadonlyRoot",
  clearSessionReadonlyRoots: "fs:clearSessionReadonlyRoots",
  /**
   * 把服务端回执里的别名写到会话授权根上（`external/<别名>/` 的唯一真相源）。
   *
   * 别名是服务端登记这条授权时 mint 的命名空间，模型与 UI 见到的都是它；桌面建根
   * 时不再自算一份。不写下来，本机引擎的 externalMounts 快照就没有这个挂载点，
   * `external/<别名>/` 恒定 PathNotFound。
   */
  adoptSessionRootAlias: "fs:adoptSessionRootAlias",
  listDir: "fs:listDir",
  listFiles: "fs:listFiles",
  readFile: "fs:readFile",
  readTextFile: "fs:readTextFile",
  writeFile: "fs:writeFile",
  rename: "fs:rename",
  move: "fs:move",
  copy: "fs:copy",
  create: "fs:create",
  delete: "fs:delete",
  watch: "fs:watch",
  unwatch: "fs:unwatch",
  changed: "fs:changed",
  workspaceOp: "fs:workspaceOp",
  grantSessionRun: "fs:grantSessionRun",
  reveal: "fs:reveal",
  openPath: "fs:openPath",
  copyPath: "fs:copyPath",
  /** 将根内相对路径移入系统回收站（软删）。 */
  trashPath: "fs:trashPath",
  /** 列出工作区 AgentCore/trash（产品一键还原；非 OS 回收站）。 */
  listWorkspaceTrash: "fs:listWorkspaceTrash",
  /** 还原一条 AgentCore/trash 条目到原相对路径。 */
  restoreWorkspaceTrash: "fs:restoreWorkspaceTrash",
  /** 列出本地工作区 AgentCore/versions 用户命名版本（创建/恢复走 sidecar）。 */
  listWorkspaceVersions: "fs:listWorkspaceVersions",
  /** 删除一个用户命名版本（命名版本永不自动清理，只有显式删）。 */
  deleteWorkspaceVersion: "fs:deleteWorkspaceVersion",
  /** 附加文件：区内引用原路径；区外才写入 attachments/ 或暂存。 */
  pickAndStageAttachment: "fs:pickAndStageAttachment",
  /** 从已授权根相对路径驻留。 */
  stageFromRoot: "fs:stageFromRoot",
  /** 拖拽绝对路径驻留（仅 preload 调用，不下发 renderer）。 */
  stageFromAbsPath: "fs:stageFromAbsPath",
  /**
   * 无磁盘路径的 File（剪贴板截图等）按字节驻留（仅 preload 调用，不下发 renderer）。
   * Electron ``webUtils.getPathForFile`` 对非盘文件返回空串时走此通道。
   */
  stageFromBytes: "fs:stageFromBytes",
  /** 草稿暂存 → 本地工作区（区内引用则跳过复制）。 */
  finalizeStagedAttachment: "fs:finalizeStagedAttachment",
  /** 云端：取出暂存字节后清除。 */
  consumeStagedBytes: "fs:consumeStagedBytes",
  /** 启动清扫：删除历史遗留、且无草稿引用的暂存目录。 */
  sweepStagingOrphans: "fs:sweepStagingOrphans",
  /**
   * 云 scratch 产物单向导出：弹目录选择器，把 zip（base64）解压落地。
   * 不登记授权根、不改工作区绑定（双模式工作区 §八.7）。
   */
  checkoutArchive: "fs:checkoutArchive",
  /**
   * 云 scratch 产物「在浏览器打开」：把 zip（base64）解压到临时目录，再用系统默认
   * 程序打开指定文件（HTML → 系统浏览器，得到完整 JS + 多文件相对资源的真实效果）。
   * 落临时目录、不弹目录、不登记根（与 checkoutArchive 的「导出落地」区分）。
   */
  previewArchive: "fs:previewArchive",
  /**
   * 单文件「另存为」：renderer 把已取到的字节交主进程，弹系统保存对话框后原子落盘。
   * Electron 不支持 `<a download>` + blob:（不触发 will-download，且 blob: 导航被
   * will-navigate 安全守卫拦截），故桌面端所有下载（云工作区文件 / 快照 zip / 对话
   * 导出 / IM 附件 / 图表·白板导出）统一走本通道；web 端保留 anchor 方案。
   */
  saveFile: "fs:saveFile",
  /**
   * 云端文件「用本机默认应用打开」：renderer 把已取到的字节交主进程，落**只读**临时副本后
   * `shell.openPath`。云端工作区文件在服务器上、本机无实体，这是它唯一的本机打开路径
   * （与本地源的 {@link FS_CHANNELS.openPath} 分工：后者开的是用户盘上的真实文件）。
   * 副本只读 = 外部程序改动不会静默丢失（Word 显示「只读」逼另存为）；本期不做回写。
   */
  openTempFile: "fs:openTempFile",
} as const;

/** {@link FsApi.checkoutArchive} 结果。 */
export type CheckoutArchiveResult =
  | { ok: true; destName: string; fileCount: number }
  | { ok: false; reason: "cancelled" }
  | { ok: false; reason: "error"; message: string };

/**
 * {@link FsApi.addRoot} 结果。
 * - `cancelled`：用户关闭选择器（非错误）
 * - `dialog_failed`：系统未能弹出目录选择器
 * - `unauthorized`：所选路径无法访问/登记为授权根
 */
export type AddRootResult =
  | { ok: true; root: FsRoot }
  | { ok: false; reason: "cancelled" }
  | {
      ok: false;
      reason: "dialog_failed" | "unauthorized";
      message: string;
    };

/** {@link FsApi.saveFile} 结果。`cancelled` = 用户在保存对话框里放弃（非错误）。 */
export type SaveFileResult =
  | { ok: true; fileName: string }
  | { ok: false; reason: "cancelled" }
  | { ok: false; reason: "error"; message: string };

/** {@link FsApi.previewArchive} 结果（落临时目录、无「取消」态）。 */
export type PreviewArchiveResult =
  | { ok: true; fileCount: number }
  | { ok: false; reason: "error"; message: string };

/**
 * {@link FsApi.openTempFile} 字节上限（64 MiB）。够覆盖 Office / PDF / 图片的常规体量，
 * 又不至于把内存与临时盘撑爆；超限由 renderer 引导走「下载」（用户主动另存为无此限）。
 */
export const OPEN_TEMP_FILE_MAX_BYTES = 64 * 1024 * 1024;

/**
 * {@link FsApi.openTempFile} 结果。`unsupported_type` = 扩展名不在安全白名单
 * （见 `shared/openable-ext.ts`；**无确认逃生口**——字节来源是 AI 产出）；
 * `too_large` = 超 {@link OPEN_TEMP_FILE_MAX_BYTES}。
 */
export type OpenTempFileResult =
  | { ok: true }
  | {
      ok: false;
      reason: "unsupported_type" | "too_large" | "error";
      message: string;
    };

/** Electron `app.getPath` keys accepted as grant_* well-known roots. */
export type GrantSessionWellKnown = "desktop" | "downloads" | "documents";

/**
 * IPC / FsApi params for {@link FsApi.grantSessionReadonlyRoot}.
 *
 * Mount-only path transport (C1 §〇): optional absolute `path` / `wellKnown`+
 * `targetName` may cross renderer→main for resolution. Success never returns abs;
 * may include `displayLabel` (basename / redacted). Abs must not persist in
 * renderer or REST grant bodies.
 */
export interface GrantSessionReadonlyRootParams {
  conversationId: string;
  mode?: "readonly" | "organize" | "attach_rw";
  /** Absolute local directory path (C1-wide). Preferred over wellKnown when set. */
  path?: string;
  wellKnown?: GrantSessionWellKnown;
  targetName?: string;
}

/** Failure reasons from grant resolve (no picker; not_found ≠ cancelled). */
export type GrantSessionReadonlyRootFailReason =
  | "not_found"
  | "permission_denied"
  | "not_directory"
  | "ambiguous"
  | "invalid";

/**
 * Result of {@link FsApi.grantSessionReadonlyRoot}.
 * Success: root id/name/alias/mode + optional displayLabel — never absPath.
 */
export type GrantSessionReadonlyRootResult =
  | { ok: true; root: FsRoot; displayLabel?: string }
  | { ok: false; reason: GrantSessionReadonlyRootFailReason; message?: string };

/** 附加文件落盘目标（区内引用原路径；区外才进 attachments/）。 */
export interface StageAttachmentDest {
  rootId: string;
  subpath?: string;
}

/**
 * 引用即驻留结果。区内文件 ``workspacePath`` 是原路径；区外才写入 ``attachments/``。
 * ``stagingId`` 仍在主进程暂存（草稿尚无会话 / 云端待上传）。绝对路径永不出现在此结构中。
 */
export interface StagedAttachment {
  name: string;
  workspacePath?: string;
  stagingId?: string;
  binary: boolean;
  text: string;
  truncated: boolean;
  sizeBytes: number;
  /**
   * 文件已在某授权根内：相对该根的 POSIX 路径（绝对路径永不下发）。
   * 草稿尚无 dest 时与 ``stagingId`` 并存，发送时若家仍是该根则引用、否则复制。
   */
  citedRootId?: string;
  citedRelPath?: string;
}

/**
 * 暴露在 `window.fsApi` 上的 renderer 端 API 面。
 *
 * `move` 的 `destRelPath` 语义为「目标目录」，源对象将被移动进该目录。
 */
export interface FsApi {
  addRoot(): Promise<AddRootResult>;
  /**
   * 取得（必要时自动创建 + 授权）默认本地容器根（`~/Documents/AgentCore`）。
   *
   * 供显式「本机草稿」裸聊与本地项目创建复用；桌面裸聊默认已切云（§八.7），
   * 新建裸聊不再自动调用。幂等——已存在同路径的授权根则原样复用。
   */
  ensureDefaultRoot(): Promise<FsRoot>;
  /**
   * 云 → 本机单向 checkout：弹目录选择器并解压 zip（纯导出，不登记授权根）。
   * 合回落点写出走 Diff / 只合回产物，不经本 API。取消 → `{ reason:"cancelled" }`。
   */
  checkoutArchive(archiveBase64: string): Promise<CheckoutArchiveResult>;
  /**
   * 「在浏览器打开」：把 zip（base64）解压到临时目录，再用系统默认程序打开
   * `openRelPath`（HTML → 系统浏览器）。与 {@link checkoutArchive} 不同：不弹目录、
   * 落临时目录、打开指定文件而非整个目录。
   */
  previewArchive?(
    archiveBase64: string,
    openRelPath: string,
  ): Promise<PreviewArchiveResult>;
  /**
   * 单文件「另存为」：弹系统保存对话框（以净化后的 `suggestedName` 预填、默认落
   * 下载目录），把 `bytes` 原子写入用户所选路径（同目录临时文件 + rename）。
   * 保存路径完全由用户经对话框选定，绝对路径不回传 renderer（只回文件名供提示）。
   * 桌面端 `saveBlob` 的落盘后端——见 {@link FS_CHANNELS.saveFile} 的为什么。
   */
  saveFile(suggestedName: string, bytes: Uint8Array): Promise<SaveFileResult>;
  /**
   * 云端文件「用本机默认应用打开」：把 `bytes` 写进独占临时目录、置只读，再
   * `shell.openPath`。不弹对话框、不登记根、绝对路径不回传 renderer。
   *
   * 主进程按 `shared/openable-ext.ts` 白名单**硬拒**名单外扩展名（renderer 门控被绕过
   * 时的强制面），并对 `suggestedName` 做与 {@link saveFile} 同款净化。optional：web
   * 预览运行时不实现，云端源据此条件挂载 `openWithOsDefaultApp`。
   */
  openTempFile?(
    suggestedName: string,
    bytes: Uint8Array,
  ): Promise<OpenTempFileResult>;
  listRoots(): Promise<FsRoot[]>;
  removeRoot(rootId: string): Promise<void>;
  /**
   * W3/P1: session root (readonly | organize | attach_rw) bound to conversation.
   * Accepts legacy `(conversationId, mode?)` or a params object with optional
   * `path` / `wellKnown` / `targetName` (resolve only — never opens a folder picker).
   * Failure reasons distinguish not_found / permission_denied / not_directory / ambiguous (≠ cancelled).
   */
  grantSessionReadonlyRoot(
    conversationIdOrParams: string | GrantSessionReadonlyRootParams,
    mode?: "readonly" | "organize" | "attach_rw",
  ): Promise<GrantSessionReadonlyRootResult>;
  listSessionReadonlyRoots(conversationId: string): Promise<FsRoot[]>;
  revokeSessionReadonlyRoot(
    conversationId: string,
    rootId: string,
  ): Promise<boolean>;
  clearSessionReadonlyRoots(conversationId: string): Promise<void>;
  /**
   * 采纳服务端回执里的权威别名（见 {@link FS_CHANNELS.adoptSessionRootAlias}）。
   * 返回该根的别名是否已与服务端一致；根不存在 / 不属于该对话 → false。
   */
  adoptSessionRootAlias(
    conversationId: string,
    rootId: string,
    alias: string,
  ): Promise<boolean>;
  listDir(rootId: string, relPath: string): Promise<FsResult<FsEntry[]>>;
  /**
   * 递归列出根内的全部文件（用于 @ 提及检索）。
   * 忽略常见无关目录 + 根 `.gitignore`，有数量上限；`truncated` 必须透出。
   * `order: "recent"` 按 mtime 倒序（会 stat，文件项带 `mtimeMs`）；
   * 默认 `"path"` 字母序且不 stat、不带 `mtimeMs`。
   */
  listFiles(
    rootId: string,
    opts?: { order?: FsListFilesOrder },
  ): Promise<FsResult<FsListFilesResult>>;
  readFile(rootId: string, relPath: string): Promise<FsResult<FilePreview>>;
  /**
   * 读完整文本文件用于**编辑**（正文 + 基线 mtime/编码/换行）。与预览 `readFile` 分工：
   * 不截断、不判别图片，二进制 / 过大 / 越界以 `FsResult` 失败返回。
   */
  readTextFile(rootId: string, relPath: string): Promise<FsResult<FsTextFile>>;
  /**
   * 写文本文件，带写前 CAS（`baselineMtimeMs`）。原子写（临时文件 + rename）；
   * 失败以判别式 `FsWriteResult` 返回（`conflict` 携磁盘当前 mtime），不抛异常。
   */
  writeFile(
    rootId: string,
    relPath: string,
    input: FsWriteInput,
  ): Promise<FsWriteResult>;
  rename(rootId: string, relPath: string, newName: string): Promise<FsResult>;
  move(
    rootId: string,
    srcRelPath: string,
    destRelPath: string,
  ): Promise<FsResult>;
  /**
   * 复制文件/目录（目录递归）到**完整目标路径** `destRelPath`（含最终名）。
   *
   * 与 `move` 的语义差异：`move` 的目标是「目录」（保名移入）；`copy` 收完整目标路径，
   * 故能表达「同目录内另存为新名」（如 `a.txt` → `a 副本.txt`）——这是去重粘贴所必需。
   * 主进程经 `fs.cp(recursive)` 实现，拒绝覆盖已存在目标与「复制进自身子树」。失败以
   * `FsResult` 返回。
   */
  copy(
    rootId: string,
    srcRelPath: string,
    destRelPath: string,
  ): Promise<FsResult>;
  create(
    rootId: string,
    relPath: string,
    kind: FsCreateKind,
  ): Promise<FsResult>;
  delete(rootId: string, relPath: string): Promise<FsResult>;
  watch(rootId: string, relPath: string): Promise<void>;
  unwatch(rootId: string, relPath: string): Promise<void>;
  /** 订阅目录变更；返回取消订阅函数。 */
  onChanged(cb: (e: FsChangedEvent) => void): () => void;
  /**
   * 在某授权根上执行一次本地工作区 op（供本地模式下 AI 工具调用回填）。
   *
   * `args` 为该 op 的相对路径载荷（如 `{ path }` / `{ directory, pattern }`）；
   * 失败不抛异常，统一以 `WorkspaceOpResult` 的类型化 `error` 返回。
   *
   * 可选顶层 `timeoutMs`（勿塞进 `args`）：主进程墙钟 Promise.race，超时先回
   * `WorkspaceIOError` 活性信封；底层 op 可能继续跑（与渲染 abort 同构）。
   *
   * 可选 `correlation`：仅观测用（conversation_id / request_id），对齐服务端
   * `workspace.op_timeout`；不改调度语义。
   */
  workspaceOp(
    rootId: string,
    op: WorkspaceOpName,
    args: Record<string, unknown>,
    timeoutMs?: number,
    correlation?: { conversationId?: string; requestId?: string },
  ): Promise<WorkspaceOpResult>;
  /**
   * 聊天内 RunConfirm「本会话都允许」→ 主进程置 session run flag（进程重启清零）。
   * 不引入永久跨天 allowlist。
   */
  grantSessionRun(): Promise<void>;
  /**
   * 在系统文件管理器中定位该路径（Windows 资源管理器 / macOS 访达 / Linux 文件管理器）。
   *
   * 主进程把 `{rootId, relPath}` 解析为绝对路径并 realpath 校验在根内后调
   * `shell.showItemInFolder`——**绝对路径不下发 renderer**，沿用本契约的安全不变量。
   * 仅本地源有意义（云端工作区文件在服务器上，无本机路径）。失败以 `FsResult` 返回。
   */
  reveal(rootId: string, relPath: string): Promise<FsResult>;
  /**
   * 用系统默认程序打开该文件（PDF / Office / 压缩包等 in-app 预览打不开的类型）。
   * 经 `shell.openPath`；同样在主进程解析 + 校验在根内。仅本地源有意义。
   */
  openPath(rootId: string, relPath: string): Promise<FsResult>;
  /**
   * 把该路径的**绝对路径**写入系统剪贴板。写入在主进程完成（`clipboard.writeText`），
   * 故绝对路径不进 renderer。仅本地源有意义。
   */
  copyPath(rootId: string, relPath: string): Promise<FsResult>;
  /**
   * 将根内相对路径移入系统回收站（`shell.trashItem`，软删）。空 `relPath`（根自身）拒绝。
   * 路径尚不存在（懒建 scratch 未物化）视为成功。仅本地源有意义。
   * **不**承诺产品一键还原——请到系统回收站手动恢复。
   */
  trashPath(rootId: string, relPath: string): Promise<FsResult>;
  /**
   * 列出本地根下 ``AgentCore/trash`` 条目（无系统回收站时的软删兜底）。
   * OS ``shell.trashItem`` 删除不会出现在此列表。
   */
  listWorkspaceTrash(rootId: string): Promise<FsResult<WorkspaceTrashEntry[]>>;
  /** 还原一条 AgentCore/trash 条目到原相对路径。 */
  restoreWorkspaceTrash(rootId: string, entryId: string): Promise<FsResult>;
  /**
   * 列出本地工作区 ``AgentCore/versions`` 用户命名版本（新 → 旧）。
   * `subpath` = 工作区在授权根内的相对子路径（裸聊 scratch / 项目子目录），
   * 根自身传 `""`。创建 / 恢复不在这里——它们走 sidecar（zip/unzip 只一份实现）。
   */
  listWorkspaceVersions(
    rootId: string,
    subpath: string,
  ): Promise<FsResult<WorkspaceVersionEntry[]>>;
  /** 删除一个用户命名版本（不可撤销；命名版本永不自动清理）。 */
  deleteWorkspaceVersion(
    rootId: string,
    subpath: string,
    versionId: string,
  ): Promise<FsResult>;
  /**
   * 附加文件：区内引用原路径；区外才复制进 ``attachments/``（有 dest）
   * 或主进程暂存（无 dest）。取消选择返回 ``null``。
   */
  pickAndStageAttachment(
    dest?: StageAttachmentDest,
  ): Promise<FsResult<StagedAttachment> | null>;
  /** 从已授权根内相对路径驻留（@ 菜单选中的文件，含二进制）。 */
  stageFromRoot(
    rootId: string,
    relPath: string,
    dest?: StageAttachmentDest,
  ): Promise<FsResult<StagedAttachment>>;
  /**
   * 拖拽/粘贴文件：preload 优先 ``webUtils.getPathForFile``；无盘路径时读 File
   * 字节走 ``stageFromBytes``。绝对路径不进入 renderer 业务状态。
   */
  stageDroppedFile(
    file: File,
    dest?: StageAttachmentDest,
  ): Promise<FsResult<StagedAttachment>>;
  /** 把暂存附件写入本地工作区 ``attachments/``。 */
  finalizeStagedAttachment(
    stagingId: string,
    dest: StageAttachmentDest,
  ): Promise<FsResult<StagedAttachment>>;
  /** 取出暂存字节供云端 ``PUT …/workspace/files``（取出后清除暂存）。 */
  consumeStagedBytes(
    stagingId: string,
  ): Promise<FsResult<{ name: string; data: Uint8Array; binary: boolean }>>;
  /**
   * 清扫 ``attach-staging/``：删除本次启动前遗留、且不在 ``liveStagingIds`` 内的暂存目录。
   * 草稿元数据只存在于渲染进程（localStorage，且有条数上限），主进程无从判断哪些
   * 暂存仍被引用，故存活集合由渲染层给出。
   */
  sweepStagingOrphans(liveStagingIds: string[]): Promise<void>;
}
