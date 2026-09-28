import { Markdown } from "@/components/chat/Markdown";
import {
  type HistoryRecord,
  type HistoryRole,
  parseHistoryTranscript,
  presentToolReceipt,
} from "@/components/chat/historyTranscript";

const ROLE_LABEL: Record<HistoryRole, string> = {
  user: "用户",
  assistant: "CEO",
  tool: "工具",
  system: "系统",
  other: "记录",
};

function turnLabel(record: HistoryRecord): string {
  if (record.role === "tool") {
    return record.name ? `工具 ${record.name}` : "工具回执";
  }
  return ROLE_LABEL[record.role];
}

function ToolReceipt({ text }: { text: string }) {
  const face = presentToolReceipt(text);
  if (face.kind === "page") {
    return (
      <div className="space-y-2">
        {face.title ? <p className="text-sm">{face.title}</p> : null}
        {face.url ? (
          <p className="break-all text-xs text-muted-foreground">{face.url}</p>
        ) : null}
        <p
          data-testid="history-tool-body"
          className="whitespace-pre-wrap text-sm"
        >
          {face.body}
        </p>
        {face.note ? (
          <p className="text-xs text-muted-foreground">{face.note}</p>
        ) : null}
      </div>
    );
  }
  if (face.kind === "code") {
    return (
      <div className="space-y-2">
        {face.caption ? <p className="text-sm">{face.caption}</p> : null}
        <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-xs">
          {face.code}
        </pre>
      </div>
    );
  }
  return <p className="whitespace-pre-wrap text-sm">{face.text}</p>;
}

function Turn({ record }: { record: HistoryRecord }) {
  return (
    <section className="space-y-2" data-testid="history-turn">
      <p className="text-xs text-muted-foreground">{turnLabel(record)}</p>
      {record.role === "tool" || record.role === "other" ? (
        <ToolReceipt text={record.text} />
      ) : (
        <Markdown content={record.text} />
      )}
    </section>
  );
}

/** Legacy prose mirrors (no `@@` header) stay on the markdown path. */
export function HistoryTranscriptView({ body }: { body: string }) {
  const records = parseHistoryTranscript(body);
  if (records == null) return <Markdown content={body} />;
  let cursor = 0;
  const turns = records.map((record) => {
    const key = `${cursor}:${record.role}:${record.name}`;
    cursor += record.text.length + 1;
    return { key, record };
  });
  return (
    <div className="space-y-4">
      {turns.map(({ key, record }) => (
        <Turn key={key} record={record} />
      ))}
    </div>
  );
}
