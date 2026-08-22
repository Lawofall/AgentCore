import { fetchLatestAndroidApk, openApkDownload } from "@/lib/androidRelease";
import { isAndroidVersionOutdated } from "@/lib/androidSemver";
import { clientVersion } from "@/lib/clientBuildInfo";
import { useSyncExternalStore } from "react";

export type AndroidUpdatePhase =
  | "idle"
  | "unsupported"
  | "checking"
  | "current"
  | "available"
  | "error";

type AndroidUpdatesState = {
  phase: AndroidUpdatePhase;
  availableVersion: string | null;
  downloadUrl: string | null;
  message: string | null;
  dismissed: boolean;
};

const initial: AndroidUpdatesState = {
  phase: "idle",
  availableVersion: null,
  downloadUrl: null,
  message: null,
  dismissed: false,
};

let state: AndroidUpdatesState = initial;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function setState(partial: Partial<AndroidUpdatesState>) {
  state = { ...state, ...partial };
  emit();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useAndroidUpdates(): AndroidUpdatesState {
  return useSyncExternalStore(
    subscribe,
    () => state,
    () => state,
  );
}

function isNativeAndroid(): boolean {
  return (
    typeof window !== "undefined" &&
    window.__NATIVE__ === true &&
    window.__NATIVE_PLATFORM__ === "android"
  );
}

export function dismissAndroidUpdate(): void {
  setState({ dismissed: true });
}

export function openAndroidDownload(): void {
  const url = state.downloadUrl;
  if (url) openApkDownload(url);
}

export async function checkAndroidUpdate(): Promise<void> {
  if (!isNativeAndroid()) {
    setState({
      phase: "unsupported",
      availableVersion: null,
      downloadUrl: null,
      message: "仅 Android 安装包支持应用内检查更新。",
    });
    return;
  }

  const local = clientVersion();
  if (local === "dev") {
    setState({
      phase: "unsupported",
      availableVersion: null,
      downloadUrl: null,
      message: "开发构建不检查更新。",
    });
    return;
  }

  setState({ phase: "checking", message: null });

  const remote = await fetchLatestAndroidApk();
  if (!remote) {
    setState({
      phase: "error",
      availableVersion: null,
      downloadUrl: null,
      message: "暂时无法获取更新信息，请稍后重试。",
    });
    return;
  }

  if (!isAndroidVersionOutdated(local, remote.version)) {
    setState({
      phase: "current",
      availableVersion: null,
      downloadUrl: null,
      message: "已是最新版本。",
    });
    return;
  }

  setState({
    phase: "available",
    availableVersion: remote.version,
    downloadUrl: remote.downloadUrl,
    message: `发现新版本 ${remote.version}`,
  });
}

export function startAndroidUpdates(): () => void {
  if (!isNativeAndroid() || clientVersion() === "dev") {
    setState({ phase: "unsupported" });
    return () => {};
  }
  void checkAndroidUpdate();
  return () => {};
}
