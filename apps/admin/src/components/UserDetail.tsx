import { CostTrendBars } from "@/components/charts";
import { ResetPasswordDialog } from "@/components/ResetPasswordDialog";
import { SetPasswordDialog } from "@/components/SetPasswordDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import {
  TableFrame,
  TableRow,
  THead,
  Td,
  Th,
} from "@/components/ui/Table";
import {
  cn,
  COST_ESTIMATE_HINT,
  fmtCompact,
  fmtEstimatedMoney,
  fmtInt,
  fmtMoney,
  fmtMs,
  fmtNanoMoney,
  fmtTime,
} from "@/lib/utils";
import { errorMessage } from "@/services/api";
import type { UsageWindow } from "@/services/adminUsage";
import type { TurnMetricLine } from "@/services/adminObservability";
import {
  type AdminConversationLine,
  type AdminUserDetail,
  type ModelCostLine,
  type SessionSummary,
  fetchUserDetail,
} from "@/services/adminUsers";
import { ArrowLeft, ExternalLink, KeyRound, MessageSquare, Monitor, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

function quotaSummary(u: AdminUserDetail["user"]): string {
  if (u.is_unlimited) return "无限额";
  const tokens = u.quota_daily_tokens ?? "继承";
  const monthCost = u.quota_monthly_cost_cny ?? "继承";
  const dayCost = u.quota_daily_cost_cny ?? "继承";
  const req = u.quota_daily_requests ?? "继承";
  return `日 ${tokens} token · 日 ${typeof dayCost === "number" ? `¥${dayCost}` : dayCost} · 月 ${typeof monthCost === "number" ? `¥${monthCost}` : monthCost} · ${req} 请求`;
}

export function UserDetail({
  userId,
  onBack,
}: {
  userId: string;
  onBack: () => void;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const [data, setData] = useState<AdminUserDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const [settingPassword, setSettingPassword] = useState(false);

  const openReplay = (conversationId: string) => {
    navigate(`/replay/${conversationId}`, { state: { from: location.pathname } });
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchUserDetail(userId));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    void load();
  }, [load]);

  const user = data?.user;
  const byok = data?.billing_mode === "byok";
  // 行级金额（趋势 / 按模型）不带 currency——同一账本窗口内币种唯一（记账走 curated
  // 人民币价卡，BYOK 估算走社区价目快照的美元），且后端无汇率换算，故符号统一取自
  // 窗口 breakdown，绝不按 billing_mode 猜。与 AnalyticsPage 同口径。
  const billedCurrency = data?.month.cost.currency;
  const estimatedCurrency =
    data?.month.estimated_cost?.currency ??
    data?.today.estimated_cost?.currency ??
    null;
  const estimateFmtCurrency = estimatedCurrency ?? billedCurrency;

  return (
    <div className="mx-auto max-w-[1200px] px-6 py-8">
      <div className="mb-4 flex items-center justify-between gap-4">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1.5 text-muted-foreground text-sm outline-none transition-colors hover:text-foreground focus-visible:text-foreground"
        >
          <ArrowLeft size={16} />
          返回用户列表
        </button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void load()}
          disabled={loading}
          aria-label="刷新"
        >
          <RefreshCw size={14} className={cn(loading && "animate-spin")} />
        </Button>
      </div>

      {loading && (
        <div className="flex items-center justify-center gap-2 rounded-xl border border-border bg-card py-16 text-muted-foreground text-sm">
          <Spinner />
          加载中…
        </div>
      )}

      {!loading && error && (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-border bg-card py-16 text-sm">
          <span className="text-destructive">{error}</span>
          <Button variant="outline" size="sm" onClick={() => void load()}>
            重试
          </Button>
        </div>
      )}

      {!loading && !error && data && user && (
        <div className="flex flex-col gap-5">
          <header className="rounded-xl border border-border bg-card p-5">
            <div className="flex items-start justify-between gap-3">
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-xl font-semibold text-foreground">
                  {user.display_name || user.username}
                </h1>
                <Badge tone={user.role === "admin" ? "primary" : "neutral"}>
                  {user.role}
                </Badge>
                <Badge
                  tone={user.status === "active" ? "success" : "destructive"}
                >
                  {user.status === "active" ? "活跃" : "已停用"}
                </Badge>
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setSettingPassword(true)}
                >
                  <KeyRound size={14} />
                  设置密码
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setResetting(true)}
                >
                  <KeyRound size={14} />
                  重置密码
                </Button>
              </div>
            </div>
            <p className="mt-1 text-muted-foreground text-sm">
              @{user.username}
              {user.email ? ` · ${user.email}` : ""}
              {" · 注册 "}
              {fmtTime(user.created_at)}
              {" · 注册 IP "}
              {user.registration_ip || "—"}
            </p>
            <p className="mt-3 text-muted-foreground text-xs">
              配额：{quotaSummary(user)}
            </p>
            <p className="mt-1.5 text-muted-foreground text-xs">
              已配服务商：{fmtInt(data.provider_count ?? 0)}
              {" · 默认对话模型："}
              {data.default_model ?? "未配置"}
              {" · 默认后台模型："}
              {data.background_model ?? "未配置"}
            </p>
          </header>

          <SessionsTable rows={data.sessions ?? []} />

          {byok && (
            <div className="rounded-xl border border-border bg-muted/40 px-4 py-3 text-muted-foreground text-xs">
              BYOK 模式：记账成本恒为 0；「估算」列按社区价目计价
              {estimatedCurrency ? `（${estimatedCurrency}）` : ""}
              ，非上游账单，且平台不做汇率换算。
            </div>
          )}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <WindowCard label="今日" window={data.today} byok={byok} />
            <WindowCard label="本月" window={data.month} byok={byok} />
          </div>

          <section className="rounded-xl border border-border bg-card p-5">
            <h2 className="mb-4 text-base font-semibold text-foreground">
              近 7 日成本趋势
            </h2>
            <CostTrendBars
              data={data.recent_daily_cost}
              currency={billedCurrency}
            />
          </section>

          <section className="overflow-hidden rounded-xl border border-border bg-card">
            <div className="border-border border-b px-5 py-3.5">
              <h2 className="text-base font-semibold text-foreground">
                近 30 日各模型用量
              </h2>
              <p className="mt-0.5 text-muted-foreground text-xs">
                按 call 明细聚合（cost_calls · 成本降序）
                {byok ? ` · ${COST_ESTIMATE_HINT}` : ""}
              </p>
            </div>
            {data.recent_by_model.length === 0 ? (
              <p className="px-5 py-8 text-center text-muted-foreground text-sm">
                近 30 日暂无模型调用记录
              </p>
            ) : (
              <TableFrame minWidth={760} className="rounded-none border-0">
                <THead>
                  <Th className="whitespace-nowrap">模型</Th>
                  <Th align="right" className="whitespace-nowrap">
                    调用次数
                  </Th>
                  <Th align="right" className="whitespace-nowrap">
                    Tokens
                  </Th>
                  <Th align="right" className="whitespace-nowrap">
                    成本{billedCurrency ? `（${billedCurrency}）` : ""}
                  </Th>
                  <Th align="right" className="whitespace-nowrap">
                    估算{estimatedCurrency ? `（${estimatedCurrency}）` : ""}
                  </Th>
                </THead>
                <tbody>
                  {data.recent_by_model.map((row: ModelCostLine) => (
                    <TableRow key={row.model}>
                      <Td className="font-medium text-foreground">
                        {row.model || "（未标注）"}
                      </Td>
                      <Td
                        align="right"
                        className="text-muted-foreground tabular-nums"
                      >
                        {fmtInt(row.calls)}
                      </Td>
                      <Td
                        align="right"
                        className="text-muted-foreground tabular-nums"
                      >
                        {fmtCompact(row.tokens_total)}
                      </Td>
                      <Td
                        align="right"
                        className="font-medium text-foreground tabular-nums"
                      >
                        {fmtNanoMoney(row.cost_total, billedCurrency)}
                      </Td>
                      <Td
                        align="right"
                        className="text-muted-foreground tabular-nums"
                        title={
                          row.cost_estimated_total > 0
                            ? COST_ESTIMATE_HINT
                            : undefined
                        }
                      >
                        {fmtNanoMoney(
                          row.cost_estimated_total,
                          estimateFmtCurrency,
                          true,
                        )}
                      </Td>
                    </TableRow>
                  ))}
                </tbody>
              </TableFrame>
            )}
          </section>

          <ConversationsTable
            rows={data.conversations}
            userId={user.id}
            onOpen={openReplay}
          />

          <RecentTurnsTable
            rows={data.recent_turns}
            userId={user.id}
            onOpen={openReplay}
          />

          {resetting && (
            <ResetPasswordDialog
              userId={user.id}
              username={user.username}
              onClose={() => setResetting(false)}
            />
          )}

          {settingPassword && (
            <SetPasswordDialog
              userId={user.id}
              username={user.username}
              onClose={() => setSettingPassword(false)}
            />
          )}
        </div>
      )}
    </div>
  );
}

function WindowCard({
  label,
  window,
  byok,
}: {
  label: string;
  window: UsageWindow;
  byok: boolean;
}) {
  const est = window.estimated_cost;
  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="text-muted-foreground text-sm">
        {byok ? `${label}估算` : `${label}总成本`}
      </div>
      <div
        className="mt-1 text-2xl font-semibold text-foreground tabular-nums"
        title={byok && est ? COST_ESTIMATE_HINT : undefined}
      >
        {byok
          ? fmtEstimatedMoney(est?.cny_total ?? 0, est?.currency)
          : fmtMoney(window.cost.cny_total, window.cost.currency)}
      </div>
      <div className="mt-4 flex items-center gap-6 text-sm">
        <Stat
          label="Token"
          value={fmtCompact(window.usage.input + window.usage.output)}
        />
        <Stat label="请求" value={fmtInt(window.requests)} />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-0.5 font-medium text-foreground tabular-nums">
        {value}
      </div>
    </div>
  );
}

function SessionsTable({ rows }: { rows: SessionSummary[] }) {
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="border-border border-b px-5 py-3.5">
        <h2 className="text-base font-semibold text-foreground">登录会话</h2>
        <p className="mt-0.5 text-muted-foreground text-xs">
          当前有效的 refresh-token 族 · 只读
        </p>
      </div>
      {rows.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground text-sm">
          <Monitor size={22} className="text-muted-foreground/60" />
          暂无活跃登录会话
        </div>
      ) : (
        <TableFrame minWidth={820} className="rounded-none border-0">
          <THead>
            <Th className="whitespace-nowrap">IP</Th>
            <Th className="whitespace-nowrap">User-Agent</Th>
            <Th className="whitespace-nowrap">平台</Th>
            <Th className="whitespace-nowrap">最近使用</Th>
            <Th className="whitespace-nowrap">创建时间</Th>
          </THead>
          <tbody>
            {rows.map((s) => (
              <TableRow key={s.id} className="align-top">
                <Td className="whitespace-nowrap text-foreground tabular-nums">
                  {s.ip || "—"}
                </Td>
                <Td className="max-w-md text-muted-foreground">
                  <span className="line-clamp-2 break-all" title={s.user_agent ?? undefined}>
                    {s.user_agent || "—"}
                  </span>
                </Td>
                <Td className="whitespace-nowrap text-muted-foreground">
                  {s.platform || "—"}
                </Td>
                <Td className="whitespace-nowrap text-muted-foreground tabular-nums">
                  {fmtTime(s.last_used_at)}
                </Td>
                <Td className="whitespace-nowrap text-muted-foreground tabular-nums">
                  {fmtTime(s.created_at)}
                </Td>
              </TableRow>
            ))}
          </tbody>
        </TableFrame>
      )}
    </section>
  );
}

function ConversationsTable({
  rows,
  userId,
  onOpen,
}: {
  rows: AdminConversationLine[];
  userId: string;
  onOpen: (conversationId: string) => void;
}) {
  const navigate = useNavigate();
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between gap-3 border-border border-b px-5 py-3.5">
        <div>
          <h2 className="text-base font-semibold text-foreground">最近会话</h2>
          <p className="mt-0.5 text-muted-foreground text-xs">
            按最近活动排序 · 点击行进入会话复盘
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          onClick={() =>
            navigate(`/conversations?user_id=${encodeURIComponent(userId)}`)
          }
        >
          <ExternalLink size={14} />
          查看全部
        </Button>
      </div>
      <TableFrame minWidth={560} className="rounded-none border-0">
        <THead>
          <Th className="whitespace-nowrap">标题</Th>
          <Th align="right" className="whitespace-nowrap">
            消息数
          </Th>
          <Th className="whitespace-nowrap">更新时间</Th>
        </THead>
        <tbody>
          {rows.map((c) => (
            <TableRow
              key={c.id}
              label={`打开会话复盘 ${c.title || "未命名会话"}`}
              onActivate={() => onOpen(c.id)}
            >
              <Td className="text-foreground">
                {c.title || (
                  <span className="text-muted-foreground italic">未命名会话</span>
                )}
              </Td>
              <Td align="right" className="text-muted-foreground tabular-nums">
                {fmtInt(c.messages)}
              </Td>
              <Td className="whitespace-nowrap text-muted-foreground tabular-nums">
                {fmtTime(c.updated_at)}
              </Td>
            </TableRow>
          ))}
        </tbody>
      </TableFrame>
      {rows.length === 0 && (
        <div className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground text-sm">
          <MessageSquare size={22} className="text-muted-foreground/60" />
          该用户暂无会话
        </div>
      )}
    </section>
  );
}

function RecentTurnsTable({
  rows,
  userId,
  onOpen,
}: {
  rows: TurnMetricLine[];
  userId: string;
  onOpen: (conversationId: string) => void;
}) {
  const navigate = useNavigate();
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between gap-3 border-border border-b px-5 py-3.5">
        <div>
          <h2 className="text-base font-semibold text-foreground">最近活动</h2>
          <p className="mt-0.5 text-muted-foreground text-xs">
            最近的回合（newest-first）· 点击行进入会话复盘
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          onClick={() =>
            navigate(
              `/conversations?user_id=${encodeURIComponent(userId)}`,
            )
          }
        >
          <ExternalLink size={14} />
          查看全部
        </Button>
      </div>
      <TableFrame minWidth={720} className="rounded-none border-0">
        <THead>
          <Th className="whitespace-nowrap">时间</Th>
          <Th className="whitespace-nowrap">状态</Th>
          <Th className="whitespace-nowrap">结束原因</Th>
          <Th align="right" className="whitespace-nowrap">
            轮数
          </Th>
          <Th align="right" className="whitespace-nowrap">
            耗时
          </Th>
        </THead>
        <tbody>
          {rows.map((t) => {
            const isError = t.status === "error";
            return (
              <TableRow
                key={t.turn_id}
                className="align-top"
                label={`打开会话复盘 ${t.conversation_id}`}
                onActivate={() => onOpen(t.conversation_id)}
              >
                <Td className="whitespace-nowrap text-muted-foreground tabular-nums">
                  {fmtTime(t.created_at)}
                </Td>
                <Td>
                  <Badge tone={isError ? "destructive" : "success"}>
                    {isError ? "失败" : "成功"}
                  </Badge>
                </Td>
                <Td className="max-w-xs text-muted-foreground">
                  <span className="line-clamp-2 break-words">
                    {isError ? (t.error ?? t.finish_reason ?? "error") : (t.finish_reason ?? "—")}
                  </span>
                </Td>
                <Td align="right" className="text-muted-foreground tabular-nums">
                  {fmtInt(t.rounds)}
                </Td>
                <Td
                  align="right"
                  className="whitespace-nowrap text-muted-foreground tabular-nums"
                >
                  {fmtMs(t.duration_ms)}
                </Td>
              </TableRow>
            );
          })}
        </tbody>
      </TableFrame>
      {rows.length === 0 && (
        <div className="py-10 text-center text-muted-foreground text-sm">
          该用户暂无回合活动
        </div>
      )}
    </section>
  );
}
