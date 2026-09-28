/**
 * 本机授权根还在表里，空子路径指向的文件夹已不在盘上（改名 / 移动 / 删除）。
 * 主进程抛给 IPC、渲染层横幅共用同一句，避免 JSON-RPC 英文截断。
 */
export function workspaceRootGoneMessage(absPath?: string): string {
  const path = absPath?.trim();
  if (path) {
    return `这个文件夹已经不在这台电脑上：${path}。请在工作区芯片里重新选择它所在的位置。`;
  }
  return "这个文件夹已经不在这台电脑上。请在工作区芯片里重新选择它所在的位置。";
}

/** 会话记着一个本机目录 id，这台电脑的授权表里没有。 */
export function workspaceRootAbsentMessage(): string {
  return "这个文件夹不在这台电脑上。请在工作区芯片里重新选择它所在的位置。";
}

/** 本机文件夹回合只能走本地引擎。这台客户端没有本地引擎时，不改走云端。 */
export function localEngineOffMessage(): string {
  return "这份文件在本机。请在持有该文件夹的电脑上打开客户端后再发。";
}
