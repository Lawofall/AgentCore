import { getTokens } from "@/api/client";
import {
  type BlockedUser,
  type UserSearchResult,
  listBlocks,
  searchUsers,
  startDm,
  unblockUser,
} from "@/api/messaging";
import { ImAvatar } from "@/pages/im/ImAvatar";
// 找人 (/im/new) — exact-match people search to start a DM, plus 黑名单 management.
//
// Search is server-visibility-filtered (任意搜人 护栏: a user who isn't discoverable, or
// who only accepts contacts, won't appear / can't be DMed — the backend enforces it and
// ships a precise zh refusal we surface). Tapping a result opens (or reuses) the DM.
import { ChevronLeft } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import "@/pages/im/im.css";

export function NewDmPage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<UserSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [blocks, setBlocks] = useState<BlockedUser[]>([]);

  useEffect(() => {
    listBlocks()
      .then(setBlocks)
      .catch(() => {
        if (!getTokens()) navigate("/login", { replace: true });
      });
  }, [navigate]);

  // Debounced search: an exact-match lookup shouldn't fire on every keystroke.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults(null);
      setError(null);
      return;
    }
    setSearching(true);
    const timer = window.setTimeout(() => {
      searchUsers(q)
        .then((r) => {
          setResults(r);
          setError(null);
        })
        .catch((e) => setError(e instanceof Error ? e.message : "搜索失败"))
        .finally(() => setSearching(false));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  async function open(userId: string) {
    setStarting(true);
    setError(null);
    try {
      const chat = await startDm(userId);
      navigate(`/im/c/${chat.id}`, { replace: true, state: { chat } });
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法发起会话");
      setStarting(false);
    }
  }

  async function unblock(targetId: string) {
    try {
      await unblockUser(targetId);
      setBlocks((bs) => bs.filter((b) => b.id !== targetId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消拉黑失败");
    }
  }

  return (
    <div className="screen">
      <header className="bar">
        <button
          type="button"
          className="link icon-btn"
          aria-label="返回"
          onClick={() => navigate("/im")}
        >
          <ChevronLeft size={20} />
        </button>
        <span className="bar-title">找人</span>
        <span className="bar-right" aria-hidden />
      </header>

      <div className="search">
        <input
          className="search-input"
          value={query}
          placeholder="按用户名或 ID 精确搜索"
          // biome-ignore lint/a11y/noAutofocus: 找人页打开即聚焦搜索框是刻意的移动端 UX（用户来此页就是为了立刻搜索）
          autoFocus
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && (
          <button
            type="button"
            className="search-clear"
            onClick={() => setQuery("")}
          >
            ✕
          </button>
        )}
      </div>

      <div className="list">
        {error && <p className="error hint">{error}</p>}

        {query.trim() ? (
          <>
            {searching && results === null && (
              <p className="muted hint">搜索中…</p>
            )}
            {results !== null && results.length === 0 && !searching && (
              <p className="muted hint">没有找到匹配的用户。</p>
            )}
            {results?.map((u) => (
              <button
                key={u.id}
                type="button"
                className="im-search-result"
                disabled={starting}
                onClick={() => void open(u.id)}
              >
                <ImAvatar
                  name={u.display_name || u.username}
                  url={u.avatar_url ?? null}
                />
                <span className="im-result-text">
                  <span className="im-name">
                    {u.display_name || u.username}
                  </span>
                  <span className="im-result-handle">@{u.username}</span>
                </span>
              </button>
            ))}
          </>
        ) : blocks.length > 0 ? (
          <>
            <div className="im-section-title">黑名单</div>
            {blocks.map((b) => (
              <div key={b.id} className="im-search-result">
                <ImAvatar
                  name={b.display_name || b.username}
                  url={b.avatar_url ?? null}
                />
                <span className="im-result-text">
                  <span className="im-name">
                    {b.display_name || b.username}
                  </span>
                  <span className="im-result-handle">@{b.username}</span>
                </span>
                <button
                  type="button"
                  className="link"
                  onClick={() => void unblock(b.id)}
                >
                  取消拉黑
                </button>
              </div>
            ))}
          </>
        ) : (
          <p className="muted hint">输入用户名或 ID 精确搜索，即可发起对话。</p>
        )}
      </div>
    </div>
  );
}
