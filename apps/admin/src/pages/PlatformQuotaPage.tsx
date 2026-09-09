import { PlatformCredentialsCard } from "@/components/PlatformCredentialsCard";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, Page, PageHeader, SectionHeader } from "@/components/ui/Page";
import {
  ErrorState,
  Refreshing,
  StaleDataNotice,
  TableSkeleton,
} from "@/components/ui/States";
import { cn, fmtCompact, fmtCny, fmtInt, nanoToYuan } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import {
  type AdminSystemStatus,
  fetchSystemStatus,
} from "@/services/adminSystem";
import { RefreshCw } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useState } from "react";

function quotaLimit(value: string): ReactNode {
  return value === "0" ? <span className="text-muted-foreground">不限</span> : value;
}

const ENV_RESTART_HINT = "改 env 后需重启、无热更。";

/**
 * 平台额度: the operator surface for the credential pool plus the deploy-time
 * billing / global-quota snapshot. Per-user overrides stay on 用户; Go window
 * usage stays on 分析·成本. This page is the only place those two facts render.
 */
export function PlatformQuotaPage() {
  const [data, setData] = useState<AdminSystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshEpoch, setRefreshEpoch] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchSystemStatus());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const refresh = useCallback(() => {
    setRefreshEpoch((n) => n + 1);
    void load();
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Page>
      <PageHeader
        title="平台额度"
        note={`${ENV_RESTART_HINT}每用户覆盖在「用户」；Go 三窗口用量在「分析 · 成本」。`}
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={refresh}
            disabled={loading}
            aria-label="刷新"
          >
            <RefreshCw size={14} className={cn(loading && "animate-spin")} />
          </Button>
        }
      />

      <div className="flex flex-col gap-5">
        {!data && loading && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <TableSkeleton rows={3} columns={2} />
            <TableSkeleton rows={5} columns={2} />
          </div>
        )}

        {!data && !loading && error && (
          <ErrorState message={error} onRetry={refresh} />
        )}

        {data && (
          <>
            {error && (
              <StaleDataNotice message={error} onRetry={refresh} />
            )}

            <Refreshing
              active={loading}
              className="grid grid-cols-1 gap-4 md:grid-cols-2"
            >
              <StatusCard title="计费模式">
                <Badge tone="primary">
                  {data.billing_mode === "byok" ? "BYOK · 自带 Key" : "平台付费"}
                </Badge>
                <p className="mt-3 text-muted-foreground text-xs">
                  {data.billing_mode === "byok"
                    ? "对话跑在用户自带的 DeepSeek Key 上，配额防线休眠。"
                    : "平台全局 Key + 配额防线生效。"}
                </p>
                <p className="mt-2 text-muted-foreground text-xs">{ENV_RESTART_HINT}</p>
              </StatusCard>

              <StatusCard title="全局配额默认值">
                <Row label="日 token">
                  {quotaLimit(
                    data.quota.daily_tokens === 0
                      ? "0"
                      : fmtCompact(data.quota.daily_tokens),
                  )}
                </Row>
                <Row label="月成本">
                  {quotaLimit(
                    data.quota.monthly_cost_nano === 0
                      ? "0"
                      : fmtCny(nanoToYuan(data.quota.monthly_cost_nano)),
                  )}
                </Row>
                <Row label="日成本">
                  {quotaLimit(
                    data.quota.daily_cost_nano === 0
                      ? "0"
                      : fmtCny(nanoToYuan(data.quota.daily_cost_nano)),
                  )}
                </Row>
                <Row label="日请求">
                  {quotaLimit(
                    data.quota.daily_requests === 0
                      ? "0"
                      : fmtInt(data.quota.daily_requests),
                  )}
                </Row>
                <p className="mt-3 text-muted-foreground text-xs">
                  0 = 不限 · 每用户可在「用户」覆盖。成本上限恒按人民币（¥）配置；BYOK
                  的估算金额自带币种、平台不做汇率换算，两者不可直接比较。
                  {ENV_RESTART_HINT}
                </p>
              </StatusCard>
            </Refreshing>
          </>
        )}

        <PlatformCredentialsCard refreshEpoch={refreshEpoch} />
      </div>
    </Page>
  );
}

function StatusCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card className="flex flex-col">
      <SectionHeader title={title} />
      <div className="p-5">{children}</div>
    </Card>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-foreground tabular-nums">{children}</span>
    </div>
  );
}
