import { useSidePanelStore } from "@/stores/sidePanel";
import {
  type SearchHitKind,
  type SearchHitSegment,
  hasSearchHits,
  parseSearchHits,
  searchHitFileName,
  searchHitPathLabel,
} from "./parseSearchHits";

/**
 * Clickable grep / code_search hit list: workspace-relative paths open the
 * conversation side-panel file preview via {@link useSidePanelStore.showFile}
 * (same File tab as 终稿路径点击). Line numbers are shown on the link;
 * scroll-to-line is deferred (pendingFilePreview seam not extended this batch).
 */
export function SearchHitResult({
  result,
  kind,
}: {
  result: string;
  kind: SearchHitKind;
}) {
  const showFile = useSidePanelStore((s) => s.showFile);
  const segments = parseSearchHits(result, kind);

  if (!hasSearchHits(segments)) {
    return (
      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted/50 px-2 py-1.5 text-xs text-muted-foreground">
        {result}
      </pre>
    );
  }

  return (
    <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted/50 px-2 py-1.5 font-mono text-xs text-muted-foreground">
      {segments.map((seg, i) => (
        <SearchHitLine
          // biome-ignore lint/suspicious/noArrayIndexKey: stable positional tool-output lines
          key={i}
          segment={seg}
          onOpen={(path) => showFile(path, searchHitFileName(path))}
        />
      ))}
    </pre>
  );
}

function SearchHitLine({
  segment,
  onOpen,
}: {
  segment: SearchHitSegment;
  onOpen: (path: string) => void;
}) {
  if (segment.type === "plain") {
    return (
      <>
        {segment.text}
        {"\n"}
      </>
    );
  }

  const label = searchHitPathLabel(segment);
  return (
    <>
      <button
        type="button"
        onClick={() => onOpen(segment.path)}
        className="cursor-pointer border-0 bg-transparent p-0 font-mono text-xs text-primary underline-offset-2 hover:underline"
        title={`在侧栏打开 ${segment.path}`}
      >
        {label}
      </button>
      {segment.rest}
      {"\n"}
    </>
  );
}
