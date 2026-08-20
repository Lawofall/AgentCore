// Memory-write notices (two-layer memory). Episodic = light tip; semantic = diff list;
// quota = always-pool / billing skip (summary + denied/holder rows).
// Mobile has no per-user firehose; ChatPage polls after message_end.
import type { MemoryUpdate } from "@/api/conversations";
import {
  MEMORY_UPDATE_ACTION_META,
  visibleMemoryUpdateItems,
} from "@/lib/memoryUpdateDisplay";
import { Brain, ChevronDown, ChevronRight, NotebookPen } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

/** 本场摘要超过此长度（或含换行）默认两行截断，可展开全文（对齐桌面 / ConclusionHero）。 */
export const EPISODIC_SUMMARY_CLAMP_CHARS = 60;

/**
 * 编译期穷尽闸（与 fold `noteUnhandledEvent` 同款）：后端加新 `kind`、`pnpm gen:types`
 * 后这里的 `never` 收不下，tsc 失败。渲染路径不抛——旧 App 遇上新 kind 不应白屏。
 */
function assertNever(_x: never): null {
  return null;
}

function scopeLabel(scope: string): string {
  return scope === "project" ? "本文件夹" : "全局";
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

function EpisodicUpdateTip({
  tip,
  createdAt,
}: {
  tip: string;
  createdAt: string;
}) {
  const [open, setOpen] = useState(false);
  const long = tip.length > EPISODIC_SUMMARY_CLAMP_CHARS || tip.includes("\n");
  const when = formatWhen(createdAt);

  return (
    <div className="mem-update mem-update-episodic">
      {long ? (
        <button
          type="button"
          className="mem-update-head mem-episodic-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          data-testid="episodic-summary-toggle"
        >
          <NotebookPen size={15} className="mem-update-icon" aria-hidden />
          <span className="mem-update-title">已记下本场摘要</span>
          <span className="mem-update-when">{when}</span>
          {open ? (
            <ChevronDown
              size={14}
              className="mem-episodic-chevron"
              aria-hidden
            />
          ) : (
            <ChevronRight
              size={14}
              className="mem-episodic-chevron"
              aria-hidden
            />
          )}
        </button>
      ) : (
        <div className="mem-update-head">
          <NotebookPen size={15} className="mem-update-icon" aria-hidden />
          <span className="mem-update-title">已记下本场摘要</span>
          <span className="mem-update-when">{when}</span>
        </div>
      )}
      <p
        className={`mem-episodic-summary${!open && long ? " is-clamped" : ""}`}
      >
        {tip}
      </p>
    </div>
  );
}

function memoryUpdateTitle(u: MemoryUpdate): string {
  const visibleItems = visibleMemoryUpdateItems(u.items);
  if (u.kind === "quota") {
    return u.summary ?? "常驻条目已满";
  }
  if (visibleItems.length > 0) {
    return "记忆已更新";
  }
  return u.summary ?? "记忆已整理";
}

function DiffMemoryUpdate({ u }: { u: MemoryUpdate }) {
  const navigate = useNavigate();
  const visibleItems = visibleMemoryUpdateItems(u.items);

  return (
    <div className="mem-update">
      <div className="mem-update-head">
        <Brain size={15} className="mem-update-icon" aria-hidden />
        <span className="mem-update-title">{memoryUpdateTitle(u)}</span>
        {visibleItems.length > 0 && (
          <span className="mem-update-count">{visibleItems.length} 项</span>
        )}
        <span className="mem-update-when">{formatWhen(u.createdAt)}</span>
      </div>
      {visibleItems.length > 0 && (
        <ul className="mem-update-list">
          {visibleItems.map((it) => {
            const meta = MEMORY_UPDATE_ACTION_META[it.action];
            const leaf = it.section ? `${it.file} · ${it.section}` : it.file;
            const removed = it.action === "remove";
            return (
              <li
                key={`${it.action}:${it.file}:${it.section}:${it.content}`}
                className="mem-item"
              >
                <span className={`mem-action ${meta.cls}`}>{meta.label}</span>
                <div className="mem-item-body">
                  <div className="mem-item-meta">
                    <span className="mem-item-leaf">{leaf}</span>
                    <span className="mem-item-scope">
                      {scopeLabel(it.scope)}
                    </span>
                  </div>
                  {it.content && (
                    <p
                      className={`mem-item-text${
                        removed ? " mem-item-removed" : ""
                      }`}
                    >
                      {it.content}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <button
        type="button"
        className="mem-update-link"
        onClick={() => navigate("/memory#updates")}
      >
        在「全局设定」中查看
        <ChevronRight size={14} aria-hidden />
      </button>
    </div>
  );
}

export function MemoryUpdateCard({ updates }: { updates: MemoryUpdate[] }) {
  const visible = updates.filter((u) =>
    u.kind === "episodic"
      ? Boolean((u.summary ?? "").trim())
      : u.items.length > 0 || Boolean((u.summary ?? "").trim()),
  );
  if (visible.length === 0) return null;

  return (
    <div className="mem-updates">
      {visible.map((u) => {
        switch (u.kind) {
          case "episodic":
            return (
              <EpisodicUpdateTip
                key={u.id}
                tip={(u.summary ?? "").trim()}
                createdAt={u.createdAt}
              />
            );
          case "semantic":
          case "quota":
            return <DiffMemoryUpdate key={u.id} u={u} />;
          default:
            return assertNever(u.kind);
        }
      })}
    </div>
  );
}
