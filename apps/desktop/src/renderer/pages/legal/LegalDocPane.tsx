import { Button } from "@/components/ui";
import { ArrowLeft } from "lucide-react";
import { LegalDocBody } from "./LegalDocBody";
import { LEGAL_DOCS } from "./content";
import type { LegalDocId } from "./types";

/** Full-pane legal reader for pre-auth (LoginPage) — back returns to the form. */
export function LegalDocPane({
  docId,
  onBack,
}: {
  docId: LegalDocId;
  onBack: () => void;
}) {
  const { title, updatedAt } = LEGAL_DOCS[docId];

  return (
    <div className="flex h-full w-full flex-col bg-background">
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-8 gap-1.5 px-2"
          onClick={onBack}
        >
          <ArrowLeft size={14} />
          返回
        </Button>
        <h1 className="min-w-0 flex-1 truncate text-sm font-medium">{title}</h1>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-2xl">
          <p className="mb-6 text-sm text-muted-foreground">
            更新日期：{updatedAt}
          </p>
          <LegalDocBody docId={docId} />
        </div>
      </div>
    </div>
  );
}
