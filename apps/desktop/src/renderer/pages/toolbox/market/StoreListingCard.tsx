import { Badge, CatalogTile } from "@/components/ui";
import { artifactColorVar } from "@/lib/catalogColors";
import {
  isOfficialAuthor,
  listingCopy,
} from "@/pages/toolbox/market/listingCopy";
import type { LucideIcon } from "lucide-react";
import { Store } from "lucide-react";

export type StoreShelfRow = {
  name: string;
  description: string;
  author: string;
  installed: boolean;
  hasUpdate: boolean;
};

export function StoreListingCard({
  row,
  onOpen,
  colorVar,
  icon: Icon = Store,
}: {
  row: StoreShelfRow;
  onOpen: () => void;
  colorVar?: string;
  icon?: LucideIcon;
}) {
  const official = isOfficialAuthor(row.author);
  const copy = listingCopy(row);
  return (
    <CatalogTile
      icon={<Icon size={18} />}
      colorVar={colorVar ?? artifactColorVar("guidelines")}
      title={copy.title}
      subtitle={row.author && !official ? row.author : undefined}
      description={copy.subtitle || undefined}
      onClick={onOpen}
      accessory={
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
        ) : undefined
      }
    />
  );
}
