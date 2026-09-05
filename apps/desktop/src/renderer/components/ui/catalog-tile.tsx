import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { CatalogIconShell } from "@/components/ui/catalog-icon-shell";
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

export interface CatalogTileProps {
  icon: ReactNode;
  colorVar: string;
  title: string;
  description?: string;
  /** Extra line under the description (author, etc.). */
  meta?: ReactNode;
  badge?: ReactNode;
  muted?: boolean;
  onClick?: () => void;
  /** Keep description to two lines unless the tile is expanded. */
  descriptionClamp?: boolean;
  /** Extra block under the copy (tool parameters). */
  children?: ReactNode;
  className?: string;
}

/**
 * Catalog shelf tile: icon plate, title, two-line description, optional badge.
 * Toolbox hub, skill store, and tool cards share this shell.
 */
export function CatalogTile({
  icon,
  colorVar,
  title,
  description,
  meta,
  badge,
  muted,
  onClick,
  descriptionClamp = true,
  children,
  className,
}: CatalogTileProps) {
  const interactive = Boolean(onClick) && !muted;
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <CatalogIconShell colorVar={colorVar} muted={muted}>
          {icon}
        </CatalogIconShell>
        {badge}
      </div>
      <div className="min-w-0">
        <h3 className="text-sm font-medium text-foreground">{title}</h3>
        {description ? (
          <p
            className={cn(
              "mt-1 text-xs text-muted-foreground",
              descriptionClamp && "line-clamp-2",
            )}
          >
            {description}
          </p>
        ) : null}
        {meta}
      </div>
    </>
  );

  return (
    <Card
      variant={interactive ? "interactive" : "default"}
      className={cn(
        "flex h-full w-full min-w-0 flex-col",
        interactive &&
          "shadow-raised transition-shadow group-hover:shadow-overlay",
        className,
      )}
    >
      {onClick ? (
        <Button
          variant="ghost"
          aria-label={title}
          onClick={onClick}
          className="group !flex h-auto w-full min-w-0 flex-col items-stretch justify-start gap-3 p-4 text-left font-normal"
        >
          {body}
        </Button>
      ) : (
        <div className="flex flex-col gap-3 p-4">{body}</div>
      )}
      {children ? <div className="px-4 pb-4">{children}</div> : null}
    </Card>
  );
}
