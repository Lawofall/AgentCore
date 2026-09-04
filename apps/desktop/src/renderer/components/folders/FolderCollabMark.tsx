import { folderCollaboratorsLabel } from "@/services/folders";
import { Users } from "lucide-react";

/** People mark on the owner's own desk root. Never says「已共享」. */
export function FolderCollabMark({ count }: { count: number }) {
  const label = folderCollaboratorsLabel(count);
  return (
    <span className="inline-flex shrink-0 text-muted-foreground" title={label}>
      <Users size={12} aria-hidden />
      <span className="sr-only">{label}</span>
    </span>
  );
}
