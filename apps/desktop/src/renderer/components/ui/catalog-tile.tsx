import { Card } from "@/components/ui/card";
import { CatalogIconShell } from "@/components/ui/catalog-icon-shell";
import { cn } from "@/lib/utils";
import type { MouseEvent, ReactNode } from "react";

/** Shelf columns: min 240px, grow with the 1200 canvas (four columns ≈ 280px). */
export const CATALOG_GRID_CLASS =
  "grid grid-cols-[repeat(auto-fill,minmax(min(240px,100%),1fr))] gap-3";

/** Assembly-page cards: min 200px, one line of copy, about five columns on the canvas. */
export const ASSEMBLY_CARD_GRID_CLASS =
  "grid grid-cols-[repeat(auto-fill,minmax(min(200px,100%),1fr))] gap-2";

const TILE_BODY_CLASS = "flex min-h-0 flex-1 flex-col gap-3 p-4";

export interface CatalogTileProps {
  /** `shelf` is the store / 官方 tile. `compact` is the assembly-page card. */
  density?: "shelf" | "compact";
  icon?: ReactNode;
  colorVar?: string;
  title: string;
  /** One line under the title, in the identity row (author, etc.). */
  subtitle?: ReactNode;
  description?: string;
  /** Top-right: status or unique identity only (已装 / 有更新 / 尚未开放 / 官方). */
  accessory?: ReactNode;
  /**
   * `description` puts a non-interactive mark on the explanation row so the
   * title keeps the full card width. Switches stay on the default corner.
   */
  accessoryPlacement?: "corner" | "description";
  /** Bottom chips: classification metadata, not status. */
  tags?: ReactNode;
  muted?: boolean;
  /** Fade the title but keep the tile openable. Off switches use this. */
  dim?: boolean;
  /** Accessible name of the open target. Defaults to the title. */
  ariaLabel?: string;
  onClick?: (event: MouseEvent<HTMLButtonElement>) => void;
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
 * Skill store (市场) and the toolbox「官方」提示词 / 工具 shelf use this shell.

 * The description slot always occupies two lines, even when copy is empty.
 *
 * Slots: icon+title(+subtitle) | accessory → description → children → tags → footer.
 * Do not put classification chips in accessory, or status in tags.
 *
 * `compact` drops the icon plate, the two-line description reserve, and the
 * bottom tag row. Title and one-line description sit left; accessory sits right.
 * `accessoryPlacement="description"` moves that mark onto the explanation row
 * and lets the explanation wrap to two lines.
 */
export function CatalogTile({
  density = "shelf",
  icon,
  colorVar,
  title,
  subtitle,
  description,
  accessory,
  accessoryPlacement = "corner",
  tags,
  muted,
  dim = false,
  ariaLabel,
  onClick,
  descriptionClamp = true,
  children,
  footer,
  className,
}: CatalogTileProps) {
  const interactive = Boolean(onClick) && !muted;
  const faded = muted || dim;

  if (density === "compact" && accessoryPlacement === "description") {
    const titleClass = cn(
      "truncate text-sm font-medium",
      faded ? "text-muted-foreground" : "text-foreground",
    );
    const explanation = (
      <>
        <h3 className={titleClass}>{title}</h3>
        {description || accessory ? (
          <span className="mt-2 flex items-start gap-2">
            {description ? (
              <span className="line-clamp-2 min-w-0 flex-1 text-xs text-muted-foreground">
                {description}
              </span>
            ) : (
              <span className="min-w-0 flex-1" />
            )}
            {accessory ? (
              <span className="flex shrink-0 items-center gap-1.5">
                {accessory}
              </span>
            ) : null}
          </span>
        ) : null}
        {children ? <div className="mt-2 min-w-0">{children}</div> : null}
      </>
    );
    return (
      <Card
        variant={interactive ? "interactive" : "default"}
        className={cn(
          "relative flex h-full w-full min-w-0 flex-col",
          className,
        )}
      >
        {interactive ? (
          <button
            type="button"
            aria-label={ariaLabel ?? title}
            onClick={onClick}
            className="w-full px-3 py-3 text-left"
          >
            {explanation}
          </button>
        ) : (
          <div className="px-3 py-3">{explanation}</div>
        )}
      </Card>
    );
  }

  if (density === "compact") {
    const compactCopy = (
      <>
        <h3
          className={cn(
            "truncate text-sm font-medium",
            faded ? "text-muted-foreground" : "text-foreground",
          )}
        >
          {title}
        </h3>
        {description ? (
          <p className="mt-2 truncate text-xs text-muted-foreground">
            {description}
          </p>
        ) : null}
        {children ? <div className="mt-2 min-w-0">{children}</div> : null}
      </>
    );
    return (
      <Card
        variant={interactive ? "interactive" : "default"}
        className={cn(
          "relative flex h-full w-full min-w-0 flex-col",
          className,
        )}
      >
        <div className="flex items-start gap-2 px-3 py-3">
          {interactive ? (
            <button
              type="button"
              aria-label={ariaLabel ?? title}
              onClick={onClick}
              className="min-w-0 flex-1 text-left"
            >
              {compactCopy}
            </button>
          ) : (
            <div className="min-w-0 flex-1">{compactCopy}</div>
          )}
          {accessory ? (
            <div className="flex shrink-0 items-center gap-1.5">
              {accessory}
            </div>
          ) : null}
        </div>
      </Card>
    );
  }
  const bottom =
    tags || footer ? (
      <div className="mt-auto flex flex-col gap-2">
        {tags ? (
          <div className="flex flex-wrap items-center gap-1.5">{tags}</div>
        ) : null}
        {footer}
      </div>
    ) : null;

  const copy = (
    <>
      <div className="flex shrink-0 items-center gap-3">
        {icon && colorVar ? (
          <CatalogIconShell colorVar={colorVar} muted={faded}>
            {icon}
          </CatalogIconShell>
        ) : null}
        <div className={cn("min-w-0 flex-1", accessory && "pr-16")}>
          <h3
            className={cn(
              "truncate text-sm font-medium",
              faded ? "text-muted-foreground" : "text-foreground",
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
      </div>
      <p
        data-slot="description"
        className={cn(
          "min-h-[2lh] text-xs text-muted-foreground",
          descriptionClamp && "line-clamp-2",
        )}
        aria-hidden={description ? undefined : true}
      >
        {description}
      </p>
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
        "relative flex h-full w-full min-w-0 flex-col",
        interactive &&
          "shadow-raised transition-shadow group-hover:shadow-overlay",
        className,
      )}
    >
      {interactive ? (
        <button
          type="button"
          aria-label={ariaLabel ?? title}
          onClick={onClick}
          className={cn(
            "flex h-full w-full min-w-0 flex-col text-left",
            TILE_BODY_CLASS,
          )}
        >
          {copy}
        </button>
      ) : (
        <div className={TILE_BODY_CLASS}>{copy}</div>
      )}
      {accessory ? (
        <div className="absolute right-4 top-4 z-10 flex items-center gap-1.5">
          {accessory}
        </div>
      ) : null}
    </Card>
  );
}
