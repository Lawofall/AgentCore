import { toolLabel } from "@/components/assistantLabels";
import {
  collectFailedToolNames,
  formatUnproductiveToolFailureHint,
  shouldShowUnproductiveToolFailureHint,
} from "@/lib/unproductiveToolFailureHint";
import type { ProcessStep } from "@agentcore/contract-types";

/**
 * 有正文的 unproductive：点名失败工具。不改正文；空正文失败横幅路径不渲染。
 */
export function UnproductiveToolFailureHint({
  finishReason,
  content,
  process,
  isStreaming,
}: {
  finishReason: string | null | undefined;
  content: string | undefined;
  process: ProcessStep[] | undefined;
  isStreaming?: boolean;
}) {
  if (isStreaming) return null;
  const failedToolNames = collectFailedToolNames(process);
  if (
    !shouldShowUnproductiveToolFailureHint({
      finishReason,
      content,
      failedToolNames,
    })
  ) {
    return null;
  }
  const text = formatUnproductiveToolFailureHint(failedToolNames, toolLabel);
  if (!text) return null;

  return (
    <output
      className="unproductive-tool-hint"
      data-testid="unproductive-tool-failure-hint"
    >
      {text}
    </output>
  );
}
