import { CapacitorHttp } from "@capacitor/core";

function isNative(): boolean {
  return typeof window !== "undefined" && window.__NATIVE__ === true;
}

const ANDROID_LATEST_JSON =
  "https://downloads.fashitianxia.xyz/android/latest.json";

export type AndroidApkRelease = {
  version: string;
  downloadUrl: string;
  filename: string;
};

export function parseAndroidLatestManifest(
  data: unknown,
): AndroidApkRelease | null {
  if (!data || typeof data !== "object") return null;
  const obj = data as {
    version?: string;
    filename?: string;
    downloadUrl?: string;
  };
  const version = String(obj.version ?? "").trim();
  const filename = String(obj.filename ?? "").trim();
  const downloadUrl = String(obj.downloadUrl ?? "").trim();
  if (!version || !filename || !downloadUrl) return null;
  return { version, filename, downloadUrl };
}

async function loadLatestJsonBody(): Promise<unknown | null> {
  if (isNative()) {
    const res = await CapacitorHttp.get({
      url: ANDROID_LATEST_JSON,
      headers: { Accept: "application/json" },
      connectTimeout: 15_000,
      readTimeout: 15_000,
      responseType: "json",
    });
    if (res.status < 200 || res.status >= 300) return null;
    return res.data ?? null;
  }
  const res = await fetch(ANDROID_LATEST_JSON, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) return null;
  return res.json();
}

export async function fetchLatestAndroidApk(): Promise<AndroidApkRelease | null> {
  try {
    const data = await loadLatestJsonBody();
    return parseAndroidLatestManifest(data);
  } catch {
    return null;
  }
}

export function openApkDownload(url: string): void {
  window.open(url, "_blank", "noopener,noreferrer");
}
