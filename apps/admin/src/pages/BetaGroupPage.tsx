import { CopyableId } from "@/components/CopyableId";
import { UserSectionTabs } from "@/components/SectionTabs";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Card, Page, PageHeader, SectionHeader } from "@/components/ui/Page";
import { Spinner } from "@/components/ui/Spinner";
import {
  EmptyState,
  ErrorState,
  Refreshing,
  TableSkeleton,
} from "@/components/ui/States";
import { TableFrame, TableRow, THead, Td, Th } from "@/components/ui/Table";
import { cn, fmtCount } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import {
  type BetaGroupModerator,
  appointBetaGroupModerator,
  listBetaGroupModerators,
  revokeBetaGroupModerator,
} from "@/services/adminBetaGroup";
import { type AdminUserListItem, listUsers } from "@/services/adminUsers";
import {
  RefreshCw,
  Shield,
  UserMinus,
  UserPlus,
  UsersRound,
} from "lucide-react";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

/**
 * A user id is an opaque token with no spaces and no `@`; a username has neither
 * constraint. Pasting `@alice` used to reach the API and come back as a bare 404,
 * which reads as "the server is broken" rather than "that's not an id".
 */
function looksLikeUsername(value: string): boolean {
  return /[\s@]/.test(value);
}

export function BetaGroupPage() {
  const [moderators, setModerators] = useState<BetaGroupModerator[]>([]);
  const [total, setTotal] = useState(0);
  /** 名册总数是否已经拿到过：没拿到时标题写「共 — 人」而不是「共 0 人」。 */
  const [totalKnown, setTotalKnown] = useState(false);
  const [chatId, setChatId] = useState<string | null>(null);
  const [title, setTitle] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [appointUserId, setAppointUserId] = useState("");
  /** The roster row behind the pasted id, when it came from the picker. */
  const [picked, setPicked] = useState<AdminUserListItem | null>(null);
  const [appointing, setAppointing] = useState(false);
  const [revoking, setRevoking] = useState<BetaGroupModerator | null>(null);

  // Lightweight user lookup (reuse admin users list; no new search surface).
  const [searchQ, setSearchQ] = useState("");
  const [searchHits, setSearchHits] = useState<AdminUserListItem[]>([]);
  const [searching, setSearching] = useState(false);
  const searchAbortRef = useRef<AbortController | null>(null);

  const loadAbortRef = useRef<AbortController | null>(null);
  const loadGenRef = useRef(0);

  const load = useCallback(async () => {
    loadAbortRef.current?.abort();
    const ac = new AbortController();
    loadAbortRef.current = ac;
    const gen = ++loadGenRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await listBetaGroupModerators(ac.signal);
      if (ac.signal.aborted || gen !== loadGenRef.current) return;
      setModerators(res.data);
      setTotal(res.total);
      setTotalKnown(true);
      setChatId(res.chat_id);
      setTitle(res.title);
    } catch (err) {
      if (ac.signal.aborted || gen !== loadGenRef.current) return;
      setError(errorMessage(err));
    } finally {
      if (!ac.signal.aborted && gen === loadGenRef.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void load();
    return () => {
      loadAbortRef.current?.abort();
      searchAbortRef.current?.abort();
    };
  }, [load]);

  useEffect(() => {
    const q = searchQ.trim();
    if (!q) {
      searchAbortRef.current?.abort();
      setSearchHits([]);
      setSearching(false);
      return;
    }
    const t = setTimeout(() => {
      searchAbortRef.current?.abort();
      const ac = new AbortController();
      searchAbortRef.current = ac;
      setSearching(true);
      void listUsers({ page: 1, pageSize: 8, q, status: "active" }, ac.signal)
        .then((res) => {
          if (ac.signal.aborted) return;
          setSearchHits(res.data);
        })
        .catch((err) => {
          if (ac.signal.aborted) return;
          toast.error(errorMessage(err));
          setSearchHits([]);
        })
        .finally(() => {
          if (!ac.signal.aborted) setSearching(false);
        });
    }, 300);
    return () => clearTimeout(t);
  }, [searchQ]);

  const handleAppoint = async (e?: FormEvent) => {
    e?.preventDefault();
    if (appointing) return;
    const userId = appointUserId.trim();
    if (!userId) {
      toast.error("请填写用户 ID，或用下方搜索选中一位用户");
      return;
    }
    if (looksLikeUsername(userId)) {
      toast.error("这看起来是用户名而不是用户 ID —— 请用下方搜索选中，或粘贴 user_id");
      return;
    }
    if (moderators.some((m) => m.id === userId)) {
      toast.error("该用户已经是内测群管理员");
      return;
    }
    setAppointing(true);
    try {
      const row = await appointBetaGroupModerator(userId);
      toast.success(`已任命 ${row.display_name || row.username} 为内测群管理员`);
      setAppointUserId("");
      setPicked(null);
      setSearchQ("");
      setSearchHits([]);
      void load();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setAppointing(false);
    }
  };

  const handleRevoke = async (mod: BetaGroupModerator) => {
    if (busyId) return;
    setBusyId(mod.id);
    try {
      await revokeBetaGroupModerator(mod.id);
      toast.success(`已撤销 ${mod.display_name || mod.username} 的管理员身份`);
      setRevoking(null);
      setModerators((prev) => prev.filter((m) => m.id !== mod.id));
      setTotal((n) => Math.max(0, n - 1));
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  const pickUser = (u: AdminUserListItem) => {
    setAppointUserId(u.id);
    setPicked(u);
    setSearchQ("");
    setSearchHits([]);
  };

  const searched = searchQ.trim().length > 0;
  const firstLoad = loading && moderators.length === 0 && !error;

  return (
    <Page>
      <PageHeader
        title="用户"
        description={`共 ${fmtCount(total, totalKnown)} 人`}
        note="群管理员只获得该群的治理能力，不会获得管理后台或平台级权限；平台 admin 已自带群治理权，无需任命。"
        actions={
          <>
            <UserSectionTabs />
            <Button
              variant="outline"
              size="sm"
              onClick={() => void load()}
              disabled={loading}
              aria-label="刷新"
            >
              <RefreshCw size={14} className={cn(loading && "animate-spin")} />
            </Button>
          </>
        }
      />

      {(title || chatId) && (
        <Card className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4 text-sm">
          <div className="flex items-center gap-2">
            <UsersRound size={16} className="text-muted-foreground" />
            <span className="font-medium text-foreground">{title ?? "内测群"}</span>
          </div>
          {chatId && (
            <div className="flex items-center gap-2 text-muted-foreground">
              <span className="text-xs">群 ID</span>
              <CopyableId value={chatId} label="chat_id" />
            </div>
          )}
        </Card>
      )}

      <Card className="mb-5">
        <SectionHeader
          title="任命管理员"
          description="输入用户 ID，或按用户名 / 显示名搜索后选中。任命会确保其已加入内测群。"
        />
        <div className="p-5">
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(e) => void handleAppoint(e)}
          >
            <label className="flex min-w-[240px] flex-1 flex-col gap-1.5">
              <span className="text-xs text-muted-foreground">用户 ID</span>
              <Input
                value={appointUserId}
                onChange={(e) => {
                  setAppointUserId(e.target.value);
                  setPicked(null);
                }}
                placeholder="粘贴 user_id"
                aria-label="用户 ID"
                disabled={appointing}
              />
            </label>
            <Button type="submit" size="sm" disabled={appointing}>
              {appointing ? <Spinner /> : <UserPlus size={14} />}
              任命
            </Button>
          </form>

          {picked && picked.id === appointUserId.trim() && (
            <p className="mt-2 text-xs text-muted-foreground">
              已选中{" "}
              <span className="font-medium text-foreground">
                {picked.display_name || picked.username}
              </span>
              （@{picked.username}）
            </p>
          )}

          <div className="mt-4">
            <label className="flex max-w-md flex-col gap-1.5">
              <span className="text-xs text-muted-foreground">搜索用户（可选）</span>
              <Input
                value={searchQ}
                onChange={(e) => setSearchQ(e.target.value)}
                placeholder="用户名 / 显示名"
                aria-label="搜索用户"
                disabled={appointing}
              />
            </label>
            {searched && (
              <div className="mt-2 max-w-md overflow-hidden rounded-lg border border-border bg-muted/30">
                {searching && searchHits.length === 0 && (
                  <div className="flex items-center gap-2 px-3 py-2.5 text-muted-foreground text-xs">
                    <Spinner />
                    搜索中…
                  </div>
                )}
                {!searching && searchHits.length === 0 && (
                  <p className="px-3 py-2.5 text-muted-foreground text-xs">
                    没有匹配的启用中用户（已停用的账号不在搜索范围内）
                  </p>
                )}
                {searchHits.map((u) => {
                  const already = moderators.some((m) => m.id === u.id);
                  return (
                    <button
                      key={u.id}
                      type="button"
                      onClick={() => pickUser(u)}
                      disabled={already}
                      className="flex w-full items-center justify-between gap-3 border-border border-b px-3 py-2 text-left text-sm outline-none last:border-0 hover:bg-accent focus-visible:bg-accent disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent"
                    >
                      <span className="min-w-0 truncate">
                        <span className="font-medium text-foreground">
                          {u.display_name || u.username}
                        </span>
                        <span className="ml-2 text-muted-foreground">@{u.username}</span>
                      </span>
                      <span className="flex shrink-0 items-center gap-1.5">
                        {already && <Badge tone="success">已是管理员</Badge>}
                        {u.role === "admin" && <Badge tone="primary">平台 admin</Badge>}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </Card>

      {firstLoad ? (
        <TableSkeleton columns={4} />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : (
        <Refreshing active={loading}>
          {moderators.length === 0 ? (
            <Card>
              <EmptyState
                icon={UsersRound}
                title="还没有内测群管理员"
                description="只列群内管理员；平台 admin 不必任命。"
              />
            </Card>
          ) : (
            <TableFrame minWidth={720}>
              <THead>
                <Th>用户</Th>
                <Th>用户 ID</Th>
                <Th>备注</Th>
                <Th align="right">操作</Th>
              </THead>
              <tbody>
                {moderators.map((m) => (
                  <TableRow key={m.id}>
                    <Td>
                      <div className="font-medium text-foreground">
                        {m.display_name || m.username}
                      </div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        @{m.username}
                      </div>
                    </Td>
                    <Td>
                      <CopyableId value={m.id} label="user_id" />
                    </Td>
                    <Td>
                      {m.is_platform_admin ? (
                        <Badge tone="primary">
                          <Shield size={12} className="mr-1" />
                          平台 admin（自带群治理）
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground">群管理员</span>
                      )}
                    </Td>
                    <Td align="right">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-destructive"
                        disabled={busyId !== null}
                        onClick={() => setRevoking(m)}
                      >
                        {busyId === m.id ? <Spinner /> : <UserMinus size={14} />}
                        撤销
                      </Button>
                    </Td>
                  </TableRow>
                ))}
              </tbody>
            </TableFrame>
          )}
        </Refreshing>
      )}

      {revoking && (
        <RevokeDialog
          moderator={revoking}
          busy={busyId === revoking.id}
          onClose={() => setRevoking(null)}
          onConfirm={() => void handleRevoke(revoking)}
        />
      )}
    </Page>
  );
}

function RevokeDialog({
  moderator,
  busy,
  onClose,
  onConfirm,
}: {
  moderator: BetaGroupModerator;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      open
      onClose={onClose}
      busy={busy}
      title="撤销内测群管理员"
      description="角色降为普通群成员，仍留在群内"
      footer={
        <>
          <Button variant="outline" size="sm" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? <Spinner /> : <UserMinus size={14} />}
            确认撤销
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted-foreground">
        确认撤销{" "}
        <span className="font-medium text-foreground">
          {moderator.display_name || moderator.username}
        </span>
        （@{moderator.username}）的内测群管理员身份？这不会影响其平台账号角色。
        {moderator.is_platform_admin &&
          "该用户是平台 admin，撤销后仍可通过平台权限治理该群。"}
      </p>
    </Dialog>
  );
}
