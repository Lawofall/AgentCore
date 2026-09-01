import { fetchLatestReleaseArtifacts } from "../_lib/releaseArtifacts.mjs";

const FALLBACK_VERSION = "0.9.18";
/** 测试版 SSG/离线回退；空 = 无 CDN 时官网隐藏「下载测试版」入口。bump-version desktop 不同步此字段。 */
const FALLBACK_BETA_VERSION = "";
const CACHE_SECONDS = 300;

/** Cloudflare Pages Function — runtime latest desktop release for /download. */
export async function onRequest() {
  const artifacts = await fetchLatestReleaseArtifacts(
    FALLBACK_VERSION,
    FALLBACK_BETA_VERSION,
  );
  return new Response(JSON.stringify(artifacts), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": `public, max-age=${CACHE_SECONDS}`,
    },
  });
}
