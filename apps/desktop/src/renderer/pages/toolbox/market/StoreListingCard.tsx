import { Badge, CatalogTile } from "@/components/ui";
import {
  isOfficialAuthor,
  listingCopy,
} from "@/pages/toolbox/market/listingCopy";
import type { SkillStoreGroup } from "@/services/skillStore";

export type StoreShelfRow = {
  name: string;
  description: string;
  author: string;
  group?: SkillStoreGroup;
  installed: boolean;
  hasUpdate: boolean;
};

export function StoreListingCard({
  row,
  onOpen,
}: {
  row: StoreShelfRow;
  onOpen: () => void;
}) {
  const official = isOfficialAuthor(row.author);
  const copy = listingCopy(row);
  const status =
    official || row.hasUpdate || row.installed ? (
      <>
        {official ? (
          <Badge tone="muted" pill>
            官方
          </Badge>
        ) : null}
        {row.hasUpdate ? (
          <Badge tone="primary" pill>
            有更新
          </Badge>
        ) : row.installed ? (
          <Badge tone="success" pill>
            已装
          </Badge>
        ) : null}
      </>
    ) : undefined;
  return (
    <CatalogTile
      density="compact"
      title={copy.title}
      description={copy.subtitle || undefined}
      onClick={onOpen}
      accessory={status}
    />
  );
}
