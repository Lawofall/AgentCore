import { getTokens } from "@/api/client";
import { type WorkspaceSummary, listWorkspaces } from "@/api/workspaces";
import { workspaceKind } from "@/lib/cloudFolder";
import { Brain, ChevronRight, Cloud, Folder } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

// 文件 tab home — 用户面「我的文件」。云端工作区按种类分组（文件夹 /
// 共享空间）；不列裸聊 `conv:` scratch（写盘进自动建桌）。本机工作区不展示。
// 工作区生命周期（新建 / 重命名 / 删除、绑定本机文件夹）仍是桌面的活，本列表
// 没有管理动作。每次进入会重拉（tab remount），对话里刚产出的文件不用手动刷新。

const CLOUD_GROUPS = [
  { kind: "folder" as const, title: "文件夹" },
  { kind: "shared" as const, title: "共享空间" },
];

export function WorkspacesPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<WorkspaceSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setItems(null);
    setError(null);
    listWorkspaces()
      .then((ws) => {
        if (!cancelled) setItems(ws);
      })
      .catch((e) => {
        if (cancelled) return;
        if (!getTokens()) {
          navigate("/login", { replace: true });
          return;
        }
        setError(e instanceof Error ? e.message : "加载工作区失败");
        setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  const clouds =
    items?.filter(
      (w) => w.location === "cloud" && workspaceKind(w.wsId) !== "conv",
    ) ?? [];
  const hasLocalOnly =
    items !== null &&
    clouds.length === 0 &&
    items.some((w) => w.location === "local");

  return (
    <div className="screen">
      <header className="bar">
        <span>我的文件</span>
      </header>

      <div className="list">
        <button
          type="button"
          className="file-row"
          onClick={() => navigate("/memory")}
        >
          <span className="file-icon" aria-hidden>
            <Brain size={16} />
          </span>
          <span className="file-row-main">
            <span className="file-name">全局设定</span>
            <span className="file-sub">会进模型的条目 · 常驻 / 按需</span>
          </span>
          <span className="file-chevron" aria-hidden>
            <ChevronRight size={18} />
          </span>
        </button>

        {items === null && !error && <p className="muted hint">加载中…</p>}
        {error && <p className="error hint">{error}</p>}
        {items !== null && clouds.length === 0 && !error && (
          <div className="file-empty">
            <p className="file-empty-title">
              {hasLocalOnly ? "还没有云端工作区" : "还没有工作区"}
            </p>
            <p className="muted hint">
              {hasLocalOnly
                ? "本地工作区请在桌面端查看。云端产出的文件会以文件夹出现在这里。"
                : "在对话里产出文件后，会以文件夹出现在这里。"}
            </p>
          </div>
        )}
        {CLOUD_GROUPS.map(({ kind, title }) => {
          const group = clouds.filter((w) => workspaceKind(w.wsId) === kind);
          if (group.length === 0) return null;
          return (
            <Fragment key={kind}>
              <p className="file-section-title">{title}</p>
              {group.map((ws) => (
                <button
                  key={ws.wsId}
                  type="button"
                  className="file-row"
                  onClick={() =>
                    navigate(`/files/${encodeURIComponent(ws.wsId)}`, {
                      state: { name: ws.name },
                    })
                  }
                >
                  <span className="file-icon file-icon-cloud-ws" aria-hidden>
                    <Folder size={16} />
                    <Cloud size={9} className="file-icon-badge" />
                  </span>
                  <span className="file-name">{ws.name}</span>
                  {!ws.hasFiles && <span className="file-tag">空</span>}
                  <span className="file-chevron" aria-hidden>
                    <ChevronRight size={18} />
                  </span>
                </button>
              ))}
            </Fragment>
          );
        })}
      </div>
    </div>
  );
}
