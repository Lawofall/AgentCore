import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { CatalogIconShell } from "@/components/ui/catalog-icon-shell";
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

/** Shelf columns: min 240px, grow with the 1200 canvas (four columns ≈ 280px). */
export const CATALOG_GRID_CLASS =
  "grid grid-cols-[repeat(auto-fill,minmax(min(240px,100%),1fr))] gap-3";

const TILE_BODY_CLASS = "flex min-h-0 flex-1 flex-col gap-3 p-4";

export interface CatalogTileProps {
  icon: ReactNode;
  colorVar: string;
  title: string;
  /** One line under the title, in the identity row (author, etc.). */
  subtitle?: ReactNode;
  description?: string;
  /** Top-right: status or unique identity only (已装 / 有更新 / 尚未开放 / 官方). */
  accessory?: ReactNode;
  /** Bottom chips: classification metadata, not status. */
  tags?: ReactNode;
  muted?: boolean;
  onClick?: () => void;
  /** Keep description to two lines. */
  descriptionClamp?: boolean;
  /** Extra body between description and the bottom chips (expanded params). */
  children?: ReactNode;
  /** Trailing affordance below tags (e.g. 调用参数). Not a primary CTA. */
  footer?: ReactNode;
  className?: string;
}

/**
 * Catalog shelf tile: identity row, two-line description, optional tags.
 * Toolbox hub, skill store, and tool cards share this shell.
 *
 * Slots: icon+title(+subtitle) | accessory → description → children → tags → footer.
 * Do not put classification chips in accessory, or status in tags.
 */
export function CatalogTile({
  icon,
  colorVar,
  title,
  subtitle,
  description,
  accessory,
  tags,
  muted,
  onClick,
  descriptionClamp = true,
  children,
  footer,
  className,
}: CatalogTileProps) {
  const interactive = Boolean(onClick) && !muted;
  const bottom =
    tags || footer ? (
      <div className="mt-auto flex flex-col gap-2">
        {tags ? (
          <div className="flex flex-wrap items-center gap-1.5">{tags}</div>
        ) : null}
        {footer}
      </div>
    ) : null;

  const body = (
    <>
      <div className="flex shrink-0 items-center gap-3">
        <CatalogIconShell colorVar={colorVar} muted={muted}>
          {icon}
        </CatalogIconShell>
        <div className="flex min-w-0 flex-1 items-start gap-2">
          <div className="min-w-0 flex-1">
            <h3
              className={cn(
                "truncate text-sm font-medium",
                muted ? "text-muted-foreground" : "text-foreground",
              )}
            >
              {title}
            </h3>
            {subtitle ? (
              <div className="mt-0.5 truncate text-xs text-muted-foreground">
                {subtitle}
              </div>
            ) : null}
          </div>
          {accessory ? (
            <div className="flex shrink-0 items-center gap-1.5">
              {accessory}
            </div>
          ) : null}
        </div>
      </div>
      {description ? (
        <p
          className={cn(
            "text-xs text-muted-foreground",
            descriptionClamp && "line-clamp-2",
          )}
        >
          {description}
        </p>
      ) : null}
      {children ? (
        <div className="flex min-h-0 min-w-0 flex-col">{children}</div>
      ) : null}
      {bottom}
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
      {interactive ? (
        <Button
          variant="ghost"
          aria-label={title}
          onClick={onClick}
          className={cn(
            "group !flex h-full w-full min-w-0 items-stretch justify-start text-left font-normal",
            TILE_BODY_CLASS,
          )}
        >
          {body}
        </Button>
      ) : (
        <div className={TILE_BODY_CLASS}>{body}</div>
      )}
    </Card>
  );
}
