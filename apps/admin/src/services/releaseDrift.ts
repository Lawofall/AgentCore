/**
 * 版本漂移判定 + 发布通道探针（只读，跑在管理员浏览器里）。
 *
 * 两类信号刻意分开处理：
 *  - **构建漂移**（控制台 ↔ API 的 git SHA）是本部署自己的信号，随系统状态一起来，
 *    永远可判定；任一侧没有构建 SHA 时是「信息未知」而非异轨（后端 `config.git_sha`
 *    默认就是 `"unknown"`，前端未注入时 `clientGitSha()` 同值）。
 *  - **发布通道探针**（桌面 CDN latest.json ↔ 官网下载页 API）是品牌运营的可选便利，
 *    与本平台是否健康无关。因此地址不写死在源码里，由构建期 env 注入
 *    （`apps/admin/.env.production`，键名见下）；未配置 = 探针关闭，不发任何跨域请求、
 *    系统页也不显示这张卡。探针读不到只算「未知」，由页面用中性视觉呈现。
 */

/** External CDN / website fetch budget — avoid hanging 总览 deploy snapshot Promise.all forever. */
export const RELEASE_DRIFT_FETCH_TIMEOUT_MS = 8_000;

/** 构建期注入的发布通道地址；本文件不保留任何默认域名。 */
interface ReleaseProbeEnv {
  /** 桌面 CDN 的 latest.json（面向用户的安装包 + updater）。 */
  readonly VITE_RELEASE_CDN_LATEST_URL?: string;
  /** 官网下载页的 runtime API。 */
  readonly VITE_RELEASE_DOWNLOAD_API_URL?: string;
}

export interface ReleaseProbeConfig {
  cdnLatestUrl: string;
  downloadApiUrl: string;
}

export interface ReleaseDriftSnapshot {
  /** Brand CDN desktop/latest.json version (user-facing installers + updater). */
  desktopCdnVersion: string | null;
  websiteDownloadVersion: string | null;
  /** 探针没读到时的说明——外部依赖 / 本机网络的中性信息，不是本平台故障。 */
  unreachable: string[];
}

/** 两端都配齐才算开启：只配一半无从比对，按未配置处理。 */
export function releaseProbeConfig(): ReleaseProbeConfig | null {
  const env = import.meta.env as ImportMetaEnv & ReleaseProbeEnv;
  const cdnLatestUrl = (env.VITE_RELEASE_CDN_LATEST_URL ?? "").trim();
  const downloadApiUrl = (env.VITE_RELEASE_DOWNLOAD_API_URL ?? "").trim();
  if (!cdnLatestUrl || !downloadApiUrl) return null;
  return { cdnLatestUrl, downloadApiUrl };
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(RELEASE_DRIFT_FETCH_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

async function probeVersion(
  url: string,
  label: string,
): Promise<{ version: string | null; unreachable: string | null }> {
  try {
    const payload = await fetchJson<{ version?: string }>(url);
    const version = String(payload.version ?? "").trim() || null;
    return { version, unreachable: null };
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    return { version: null, unreachable: `${label}: ${reason}` };
  }
}

/** null = 探针未配置（本地开发 / 自建部署），调用方应整块隐藏而非报错。 */
export async function fetchReleaseDrift(): Promise<ReleaseDriftSnapshot | null> {
  const config = releaseProbeConfig();
  if (!config) return null;

  const [cdn, website] = await Promise.all([
    probeVersion(config.cdnLatestUrl, "下载 CDN"),
    probeVersion(config.downloadApiUrl, "下载页 API"),
  ]);

  return {
    desktopCdnVersion: cdn.version,
    websiteDownloadVersion: website.version,
    unreachable: [cdn.unreachable, website.unreachable].filter(
      (reason): reason is string => reason !== null,
    ),
  };
}

export function versionsMatch(a: string | null, b: string | null): boolean | null {
  if (!a || !b) return null;
  return a === b;
}

/** 后端 `config.git_sha` 与前端 `clientGitSha()` 都用这个占位表示「没注入构建信息」。 */
const UNKNOWN_SHA = "unknown";

function knownSha(sha: string | null | undefined): string | null {
  const trimmed = (sha ?? "").trim();
  if (!trimmed || trimmed.toLowerCase() === UNKNOWN_SHA) return null;
  return trimmed.toLowerCase();
}

/**
 * 控制台构建 SHA ↔ API 构建 SHA 是否同一次部署。
 *
 * 返回 null = 至少一侧没有构建 SHA（未注入 / 占位 `unknown`），信息未知，**不是**异轨；
 * 两侧长度可能不同（短 SHA 7 位 vs 完整 40 位），故按较短者做前缀比较。
 */
export function buildShasMatch(
  a: string | null | undefined,
  b: string | null | undefined,
): boolean | null {
  const left = knownSha(a);
  const right = knownSha(b);
  if (!left || !right) return null;
  const width = Math.min(left.length, right.length);
  return left.slice(0, width) === right.slice(0, width);
}
