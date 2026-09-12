import {
  type PresentGitRepoStatus,
  fetchGitRepoStatus,
} from "@/lib/gitRepoStatus";
import { useCallback, useEffect, useRef, useState } from "react";

const POLL_MS = 15_000;

/**
 * U1/U2：本地有 root 时拉 Git 摘要（分支 / dirty / 变更列表）。
 * 云端 / 无 root / 无仓 → ``null``（调用方不渲染）。
 *
 * Chip 给人看：focus + 15s 轮询。不订工作区 ``onChanged``——Agent 写盘会把
 * ``git status`` 打进主进程 IPC，和本机回合的 workspace op 抢同一条热路径。
 * 用户刚 stage/commit 可走返回的 ``refresh``。
 */
export function useGitRepoStatus(
  rootId: string | null | undefined,
  enabled: boolean,
): { status: PresentGitRepoStatus | null; refresh: () => void } {
  const [status, setStatus] = useState<PresentGitRepoStatus | null>(null);
  const genRef = useRef(0);

  const refresh = useCallback(async () => {
    if (!enabled || !rootId) {
      genRef.current += 1;
      setStatus(null);
      return;
    }
    const gen = ++genRef.current;
    const next = await fetchGitRepoStatus(rootId);
    if (gen !== genRef.current) return;
    setStatus(next);
  }, [enabled, rootId]);

  // rootId / enabled 切换或卸载：丢弃在途结果。
  // biome-ignore lint/correctness/useExhaustiveDependencies: deps 故意含 enabled/rootId，切换时跑 cleanup bump gen
  useEffect(() => {
    return () => {
      genRef.current += 1;
    };
  }, [enabled, rootId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!enabled || !rootId) return;
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => {
      window.removeEventListener("focus", onFocus);
      window.clearInterval(timer);
    };
  }, [enabled, rootId, refresh]);

  return { status, refresh };
}
