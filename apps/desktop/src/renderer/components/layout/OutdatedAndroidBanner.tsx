import { Button } from "@/components/ui";
import {
  dismissAndroidUpdate,
  openAndroidDownload,
  useAndroidUpdates,
} from "@/lib/androidUpdates";

export function OutdatedAndroidBanner() {
  const { availableVersion, dismissed, downloadUrl } = useAndroidUpdates();
  if (!availableVersion || dismissed || !downloadUrl) return null;

  return (
    <output className="flex items-center gap-2 border-b border-border bg-muted/60 px-3 py-2 text-sm">
      <span className="min-w-0 flex-1">有新版本 {availableVersion} 可下载</span>
      <Button size="sm" onClick={() => openAndroidDownload()}>
        去下载
      </Button>
      <Button
        variant="ghost"
        size="sm"
        aria-label="关闭"
        onClick={() => dismissAndroidUpdate()}
      >
        ×
      </Button>
    </output>
  );
}
