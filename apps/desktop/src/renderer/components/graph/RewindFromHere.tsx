/**
 * From a sealed segment: the next window keeps the original request, that
 * segment and its ancestors' conclusions, and one scar line. Files stay
 * unless the checkbox is on. The assistant waits for the next sentence.
 */

import { Button } from "@/components/ui";
import {
  rewindErrorText,
  submitGraphRewind,
} from "@/services/graphRewind";
import { useExecutionStore } from "@/stores/execution";
import { useState } from "react";
import { toast } from "sonner";

export function RewindFromHere({
  conversationId,
  messageId,
  runId,
}: {
  conversationId: string;
  messageId: string;
  runId: string;
}) {
  const [restoreFiles, setRestoreFiles] = useState(false);
  const [busy, setBusy] = useState(false);

  const onContinue = async () => {
    setBusy(true);
    try {
      const result = await submitGraphRewind(conversationId, {
        messageId,
        targetRunId: runId,
        restoreFiles,
      });
      useExecutionStore.getState().recordFrame(
        {
          t: Date.now(),
          kind: "graph_rewind",
          targetRunId: result.target_run_id,
          cutRunIds: result.cut_run_ids,
          scar: result.scar,
        },
        messageId,
      );
      toast(
        result.files_restored
          ? "已回到这一段，文件也恢复成当时的样子。等你的下一句话。"
          : "已回到这一段。文件没动。等你的下一句话。",
      );
    } catch (error) {
      toast(rewindErrorText(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-4 space-y-2">
      <Button variant="outline" disabled={busy} onClick={() => void onContinue()}>
        从这里继续
      </Button>
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        <input
          type="checkbox"
          checked={restoreFiles}
          onChange={(event) => setRestoreFiles(event.target.checked)}
        />
        同时恢复当时的文件
      </label>
    </div>
  );
}
