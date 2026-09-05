import { SettingsStack } from "@/components/settings";
import { PageHeader } from "@/components/ui";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { Navigate, useParams } from "react-router-dom";
import { LegalDocBody } from "./LegalDocBody";
import { getLegalDoc } from "./content";
import type { LegalDocId } from "./types";

/** Authenticated settings route: /more/legal/:docId */
export function LegalSettingsPage() {
  const { docId } = useParams<{ docId: string }>();
  const doc = getLegalDoc(docId);
  if (!doc) return <Navigate to={APP_PATHS.more.about} replace />;

  return (
    <div>
      <PageHeader title={doc.title} meta={`更新日期：${doc.updatedAt}`} />
      <SettingsStack className="max-w-2xl">
        <LegalDocBody docId={doc.id as LegalDocId} />
      </SettingsStack>
    </div>
  );
}
