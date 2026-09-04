/**
 * 跨源搬运（移动 / 复制）——把「两棵树之间」的一次拖拽或粘贴，翻译成**一个**已有
 * 工作区内的 move / copy。
 *
 * 为什么需要这层：文件中枢把每个云文件夹渲染成独立的 {@link FileSource}（`folder:<id>`），
 * 子文件夹被 `hideRootDirs` 从父树摘掉后自成一根。但盘上它们并不是彼此独立的空间——
 * 云文件夹按 `rel_path` **真嵌套**（`设计/图标` 就躺在 `设计` 里面，见
 * `docs/02-架构/双模式工作区.md` §5.4），所以「父文件夹 → 子文件夹」这类搬运，本来就
 * 落在同一个物理目录树内。
 *
 * 于是不需要新的后端端点：找一个 rel_path 同时是两端前缀的云文件夹当**桥**，把两端路径
 * 改写成它的相对路径，再走既有的 `/v1/workspaces/{ws_id}/{move,copy}`。父↔子、兄弟、
 * 以及同一顶层文件夹子树内的任意两点都能这样接上。
 *
 * 接不上的组合（两个顶层文件夹之间、本机↔云端、文件夹↔对话 scratch）没有
 * 公共工作区可借，既有端点表达不了；此处返回 `null`，由调用方按 {@link CROSS_SOURCE_UNSUPPORTED}
 * 诚实说明，而不是偷偷「下载再上传」凑一个语义不同的结果。
 */

import { dedupeName } from "@/components/files/dedupeName";
import { baseName, joinPath } from "@/lib/fileSource";
import type { FolderMeta } from "@/services/folders";
import { wsCopyFile, wsListFiles, wsMoveFile } from "@/services/workspaces";

/** 云文件夹源的 id 形态（`makeCloudSource` 用 `workspace:${wsId}` 作 id）。 */
const CLOUD_FOLDER_SOURCE_ID = /^workspace:folder:(.+)$/;

/** 接不上时给用户的话——说清能做什么、不能做什么，不假装是一次失败的重试。 */
export const CROSS_SOURCE_UNSUPPORTED =
  "只能在同一个顶层文件夹的树里搬运。跨顶层文件夹、本机与云端之间还不支持，可先下载再上传。";

/** 一次改写到「桥工作区」坐标系后的搬运。 */
export interface BridgedTransfer {
  /** 承载这次 move / copy 的工作区——两端路径都在它根下。 */
  wsId: string;
  /** 源路径（桥工作区相对）。 */
  srcPath: string;
  /** 目标目录（桥工作区相对；`""` = 桥的根）。 */
  dstDir: string;
}

function normalizeRel(rel: string | null | undefined): string {
  return (rel ?? "").replace(/^\/+|\/+$/g, "");
}

/** 源 id → 它在云端树里的 rel_path；非云文件夹源（本机 / scratch / 共享）返回 null。 */
function cloudFolderRelPath(
  folders: readonly FolderMeta[],
  sourceId: string,
): string | null {
  const id = CLOUD_FOLDER_SOURCE_ID.exec(sourceId)?.[1];
  if (!id) return null;
  const folder = folders.find((f) => f.id === id);
  if (!folder || folder.mode !== "cloud") return null;
  return normalizeRel(folder.relPath) || null;
}

/** 两个 rel_path 的公共前缀，按整段比（`设计/图标` 与 `设计/图表` → `设计`）。 */
function commonAncestorRel(a: string, b: string): string {
  const left = a.split("/");
  const right = b.split("/");
  const shared: string[] = [];
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    if (left[i] !== right[i]) break;
    shared.push(left[i]);
  }
  return shared.join("/");
}

/**
 * 从 `rel` 往上找到最深的、真实存在的云文件夹。
 *
 * 往上走是因为中间层不保证也是文件夹行——列表 stale 或旧数据可能让 `设计/子/图标` 的
 * `设计/子` 没有对应记录，但 `设计` 仍然物理上含住它，照样能当桥。
 */
function deepestCloudFolderAt(
  folders: readonly FolderMeta[],
  rel: string,
): FolderMeta | null {
  let cur = rel;
  while (cur) {
    const hit = folders.find(
      (f) => f.mode === "cloud" && normalizeRel(f.relPath) === cur,
    );
    if (hit) return hit;
    const cut = cur.lastIndexOf("/");
    if (cut < 0) break;
    cur = cur.slice(0, cut);
  }
  return null;
}

/** 把某个源内路径改写成桥工作区的相对路径。 */
function rebase(sourceRel: string, bridgeRel: string, path: string): string {
  const sub =
    sourceRel === bridgeRel ? "" : sourceRel.slice(bridgeRel.length + 1);
  if (!sub) return path;
  return path ? `${sub}/${path}` : sub;
}

/**
 * 找出能承载这次跨源搬运的工作区，并把两端路径改写到它的坐标系；接不上返回 `null`。
 *
 * 同源调用同样返回 `null`——同源走各自 {@link FileSource} 的 move / copy 即可，不必绕 REST。
 */
export function resolveBridgedTransfer(
  src: { sourceId: string; path: string },
  dst: { sourceId: string; dir: string },
  folders: readonly FolderMeta[],
): BridgedTransfer | null {
  if (src.sourceId === dst.sourceId) return null;
  const srcRel = cloudFolderRelPath(folders, src.sourceId);
  const dstRel = cloudFolderRelPath(folders, dst.sourceId);
  if (!srcRel || !dstRel) return null;
  const bridge = deepestCloudFolderAt(
    folders,
    commonAncestorRel(srcRel, dstRel),
  );
  if (!bridge) return null;
  const bridgeRel = normalizeRel(bridge.relPath);
  return {
    wsId: `folder:${bridge.id}`,
    srcPath: rebase(srcRel, bridgeRel, src.path),
    dstDir: rebase(dstRel, bridgeRel, dst.dir),
  };
}

/** 目标层已有的名字（复制去重用）。 */
async function siblingNames(wsId: string, dir: string): Promise<Set<string>> {
  const listing = await wsListFiles(wsId, { dir: dir || "." });
  return new Set(listing.files.map((f) => baseName(f.path)));
}

/**
 * 落地一次桥接搬运。移动撞名直接由服务端 422 报出来（不静默改名）；复制沿用树内粘贴的
 * 「副本 / 副本 2…」去重，所以可以对同一处重复粘贴。
 */
export async function applyBridgedTransfer(
  transfer: BridgedTransfer,
  op: "move" | "copy",
): Promise<void> {
  const { wsId, srcPath, dstDir } = transfer;
  if (dstDir === srcPath || dstDir.startsWith(`${srcPath}/`)) {
    throw new Error("不能搬到自身或其子目录");
  }
  const name = baseName(srcPath);
  if (op === "move") {
    const dst = joinPath(dstDir, name);
    if (dst === srcPath) return;
    await wsMoveFile(wsId, srcPath, dst);
    return;
  }
  const dst = joinPath(
    dstDir,
    dedupeName(name, await siblingNames(wsId, dstDir)),
  );
  await wsCopyFile(wsId, srcPath, dst);
}
