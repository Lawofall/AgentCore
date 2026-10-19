/**
 * 本地工作区「命名版本」四件套 —— 云端快照四件套（`services/workspace.ts` 的
 * `listSnapshots` / `createSnapshot` / `restoreSnapshot`）的本机对位实现。
 * 产品入口在文件页「本机文件夹」根右键「版本…」（与云端同一块面板，后端走本模块）。
 *
 * 盘上落在 `AgentCore/versions/<version_id>/{meta.json,content.zip}`（内部区，不会被
 * grep / 索引 / 下个回合基线看见）。分两条 IPC：
 *
 * - **创建 / 恢复** 走 sidecar JSON-RPC —— zip / unzip 只在 Python 侧留一份实现；
 * - **列举 / 删除** 只是读目录 + 读 json + 删目录，走更轻的 `window.fsApi`。
 *
 * 与回合基线（`localTurnBaselines.ts`）分轨：基线是 best-effort、失败静默；命名版本是
 * **显式动作**，任何失败都抛出，绝不假装留成了。
 *
 * 保留策略：用户命名版本永不自动清理，只有 {@link deleteLocalVersion} 会删。
 */

import type { FsApi, FsErrorCode } from "@shared/ipc-contract";
import type { SidecarWorkspaceVersionResult } from "@shared/sidecar-contract";

/** 本机工作区寻址：授权根 + 根内子路径（裸聊 scratch / 项目子目录；根自身为空）。 */
export interface LocalWorkspaceTarget {
  rootId: string;
  subpath?: string;
}

/** 一个命名版本；字段与云端 `WorkspaceSnapshot` 对位（`name` ↔ `label`）。 */
export interface WorkspaceVersion {
  versionId: string;
  /** 用户起的版本名（云端 label 可空；本地命名版本恒非空）。 */
  name: string;
  createdAt: string;
  sizeBytes: number;
}

/** 本机版本操作失败；`code` 供调用方分支（禁止匹配中文文案）。 */
export class LocalVersionError extends Error {
  readonly code: FsErrorCode;
  constructor(reason: string, code: FsErrorCode) {
    super(reason);
    this.name = "LocalVersionError";
    this.code = code;
  }
}

const fromWire = (v: SidecarWorkspaceVersionResult): WorkspaceVersion => ({
  versionId: v.version_id,
  name: v.name,
  createdAt: v.created_at,
  sizeBytes: v.size_bytes,
});

function requireFsApi(): FsApi {
  const fsApi = typeof window !== "undefined" ? window.fsApi : undefined;
  if (!fsApi) {
    throw new LocalVersionError("此环境不支持本机版本", "unauthorized");
  }
  return fsApi;
}

/**
 * 列出该工作区的命名版本（新 → 旧）。版本区还没建过 = 空列表；读盘真失败则抛出
 * ——「读不到」不能显示成「没有版本」，那会让用户以为版本丢了。
 */
export async function listLocalVersions(
  target: LocalWorkspaceTarget,
): Promise<WorkspaceVersion[]> {
  const res = await requireFsApi().listWorkspaceVersions(
    target.rootId,
    target.subpath ?? "",
  );
  if (!res.ok) throw new LocalVersionError(res.reason, res.code);
  return res.data;
}

/** 留一个命名版本（zip 当前工作区）；空名 / 超限 / 写盘失败都 reject。 */
export async function createLocalVersion(
  target: LocalWorkspaceTarget,
  name: string,
): Promise<WorkspaceVersion> {
  const raw = await window.sidecarApi.createWorkspaceVersion({
    rootId: target.rootId,
    subpath: target.subpath,
    name,
  });
  return fromWire(raw);
}

/**
 * 恢复到某个命名版本：overlay 解压回工作区（**不清空**——版本之后新建的文件保留，
 * 与本机回合基线回退语义一致）。
 */
export async function restoreLocalVersion(
  target: LocalWorkspaceTarget,
  versionId: string,
): Promise<WorkspaceVersion> {
  const raw = await window.sidecarApi.restoreWorkspaceVersion({
    rootId: target.rootId,
    subpath: target.subpath,
    versionId,
  });
  return fromWire(raw);
}

/** 删除一个命名版本（不可撤销）。 */
export async function deleteLocalVersion(
  target: LocalWorkspaceTarget,
  versionId: string,
): Promise<void> {
  const res = await requireFsApi().deleteWorkspaceVersion(
    target.rootId,
    target.subpath ?? "",
    versionId,
  );
  if (!res.ok) throw new LocalVersionError(res.reason, res.code);
}
