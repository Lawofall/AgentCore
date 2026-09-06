/**
 * 本机 Host 能力 —— 主进程履行（Win 优先探测；mac/linux stub 同 schema）。
 *
 * 运输层对标 workspace：renderer 收到 `host_op_required` 后经
 * 本 IPC 执行，再 resolveInteraction 回填；不经 BrowserBridge loopback。
 *
 * 实现按 op 域拆到本目录；`../host-service.ts` 保持历史 import 路径稳定。
 */

export { runHostOp } from "./dispatch";
export { registerHostIpc } from "./ipc";
