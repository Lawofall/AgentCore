import { cn } from "@/lib/utils";
import type { CapabilityPack } from "@/services/capabilities";

/**
 * Capability-pack overview: name / summary / nested skills as a list.
 * Display-only availability — deployment gate decides listing; no user toggle.
 * In the 提示词阅读器, click a skill to open it in the detail pane.
 */
export function PackOverview({
  pack,
  onSelectSkill,
  heading = true,
}: {
  pack: CapabilityPack;
  onSelectSkill?: (skillName: string) => void;
  heading?: boolean;
}) {
  return (
    <div data-capability-pack={pack.id}>
      {heading ? (
        <div className="mb-3">
          <h3 className="font-medium text-foreground text-sm">{pack.name}</h3>
          <p className="mt-1 text-muted-foreground text-xs">{pack.summary}</p>
        </div>
      ) : null}

      {pack.skills.length > 0 ? (
        <div>
          <p className="mb-1.5 text-muted-foreground text-xs">包内技能</p>
          <ul className="flex flex-col gap-0.5">
            {pack.skills.map((skill) => {
              const rowClass = cn(
                "w-full rounded-lg px-2 py-1.5 text-left text-foreground text-sm",
                onSelectSkill &&
                  "transition-colors hover:bg-accent hover:text-accent-foreground",
              );
              return (
                <li key={skill.name}>
                  {onSelectSkill ? (
                    <button
                      type="button"
                      className={rowClass}
                      onClick={() => onSelectSkill(skill.name)}
                    >
                      {skill.summary}
                    </button>
                  ) : (
                    <div className={rowClass}>{skill.summary}</div>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/** Alias for the offline capability-pack preview import path. */
export const CapabilityPackCard = PackOverview;
