import { Badge } from "@/components/ui/Badge";
import { Card, SectionHeader } from "@/components/ui/Page";
import {
  ErrorState,
  Refreshing,
  StaleDataNotice,
  TableSkeleton,
} from "@/components/ui/States";
import {
  clientGitSha,
  clientVersion,
  formatGitSha,
} from "@/lib/clientBuildInfo";
import { fmtInt, fmtTime } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import {
  type AdminSystemStatus,
  fetchSystemStatus,
} from "@/services/adminSystem";
import {
  buildShasMatch,
  fetchReleaseDrift,
  type ReleaseDriftSnapshot,
  versionsMatch,
} from "@/services/releaseDrift";
import { type ReactNode, useCallback, useEffect, useState } from "react";

function orUnknown(s: string): string {
  return !s || s === "unknown" ? "未知" : s;
}

/**
 * 构建时间 → 全站统一的 MM-DD HH:mm，本机时区。
 *
 * 总览上的部署快照没有按 UTC 日切的统计窗口同屏（那种时间列才用 `fmtTimeUtc`），
 * 运维问的是「这套后端是我这边几点起来的」。无法解析的值由 `fmtTime` 原样返回。
 */
function fmtBuiltAt(raw: string): string {
  if (!raw || raw === "unknown") return "未知";
  return fmtTime(raw);
}

/**
 * Deploy-only snapshot that used to live on its own 系统 page: version,
 * release-channel drift, and account census. Billing / global quota stay on 供给·额度.
 */
export function DeploySnapshot({ reloadKey = 0 }: { reloadKey?: number }) {
  const [data, setData] = useState<AdminSystemStatus | null>(null);
  const [drift, setDrift] = useState<ReleaseDriftSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [system, releaseDrift] = await Promise.all([
        fetchSystemStatus(),
        fetchReleaseDrift(),
      ]);
      setData(system);
      setDrift(releaseDrift);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  if (!data && loading) {
    return (
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 4 }, (_, i) => (
          <TableSkeleton key={`card-${i}`} rows={4} columns={2} />
        ))}
      </div>
    );
  }

  if (!data && !loading && error) {
    return <ErrorState message={error} onRetry={() => void load()} />;
  }

  if (!data) return null;

  return (
    <div className="flex flex-col gap-5">
      {error && <StaleDataNotice message={error} onRetry={() => void load()} />}

      <Refreshing
        active={loading}
        className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
      >
        <StatusCard title="数据库">
          <Badge tone={data.database_ok ? "success" : "destructive"}>
            {data.database_ok ? "正常" : "不可达"}
          </Badge>
          <p className="mt-3 text-muted-foreground text-xs">
            实时探测（SELECT 1），与 /readyz 同源。
          </p>
        </StatusCard>

        <StatusCard title="版本">
          <Row label="控制台版本">{clientVersion()}</Row>
          <Row label="控制台构建">{formatGitSha(clientGitSha())}</Row>
          <Row label="API 版本">{orUnknown(data.version)}</Row>
          <Row label="API 构建">{orUnknown(data.git_sha).slice(0, 12)}</Row>
          <Row label="API 构建时间">{fmtBuiltAt(data.built_at)}</Row>
          <Row label="控制台 ↔ API">
            <DriftBadge
              ok={buildShasMatch(clientGitSha(), data.git_sha)}
              okLabel="同部署"
              warnLabel="异轨"
              unknownLabel="未知"
            />
          </Row>
          <p className="mt-3 text-muted-foreground text-xs">
            任一侧没有构建 SHA（本地开发 / 自建部署未注入）时判定为「未知」，不是异轨。
            构建时间为本机时区，格式 MM-DD HH:mm。
          </p>
        </StatusCard>

        {drift && (
          <StatusCard title="发布漂移">
            <Row label="CDN 最新">{drift.desktopCdnVersion ?? "—"}</Row>
            <Row label="下载页展示">
              {drift.websiteDownloadVersion ?? "—"}
            </Row>
            <Row label="CDN ↔ 下载页">
              <DriftBadge
                ok={versionsMatch(
                  drift.desktopCdnVersion,
                  drift.websiteDownloadVersion,
                )}
                okLabel="一致"
                warnLabel="不一致"
                unknownLabel="未知"
              />
            </Row>
            {drift.unreachable.length > 0 && (
              <p className="mt-3 text-muted-foreground text-xs">
                探针未读到：{drift.unreachable.join(" · ")}
              </p>
            )}
            <p className="mt-3 text-muted-foreground text-xs">
              品牌发布通道的可选探针（CDN latest.json ↔
              官网下载页），直接从你的浏览器拉取。读不到只说明这两个外部依赖或本机网络不通，与本平台健康无关。
            </p>
          </StatusCard>
        )}

        <StatusCard title="账号">
          <Row label="总数">{fmtInt(data.users_total)}</Row>
          <Row label="活跃">{fmtInt(data.users_active)}</Row>
          <Row label="管理员">{fmtInt(data.admins)}</Row>
        </StatusCard>
      </Refreshing>
    </div>
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

function DriftBadge({
  ok,
  okLabel,
  warnLabel,
  unknownLabel,
}: {
  ok: boolean | null;
  okLabel: string;
  warnLabel: string;
  unknownLabel: string;
}) {
  if (ok === null) {
    return <Badge tone="neutral">{unknownLabel}</Badge>;
  }
  return <Badge tone={ok ? "success" : "warning"}>{ok ? okLabel : warnLabel}</Badge>;
}
