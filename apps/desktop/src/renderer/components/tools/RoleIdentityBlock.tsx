import { PromptDocument } from "@/components/prompt/PromptDocument";
import { SegmentedControl } from "@/components/ui";
import { cn } from "@/lib/utils";
import { useState } from "react";

export type RoleId = "ceo" | "nested" | "leaf";

export const ROLE_ITEMS: readonly { id: RoleId; label: string }[] = [
  { id: "ceo", label: "主 Agent" },
  { id: "nested", label: "可再委派的队员" },
  { id: "leaf", label: "叶子队员" },
];

/** Mutually exclusive role `<身份>` switcher — one tab at a time, never stacked. */
export function RoleIdentityBlock({
  ceoIdentity,
  nestedIdentity,
  leafIdentity,
  className,
}: {
  ceoIdentity: string;
  nestedIdentity: string;
  leafIdentity: string;
  className?: string;
}) {
  const [role, setRole] = useState<RoleId>("ceo");
  const text =
    role === "ceo"
      ? ceoIdentity
      : role === "nested"
        ? nestedIdentity
        : leafIdentity;

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col", className)}>
      <div className="shrink-0 px-3 pt-3">
        <SegmentedControl
          aria-label="角色身份"
          value={role}
          onChange={setRole}
          items={ROLE_ITEMS.map((item) => ({
            value: item.id,
            label: item.label,
            id: `role-tab-${item.id}`,
            "aria-controls": "role-identity-panel",
          }))}
        />
      </div>
      <div
        id="role-identity-panel"
        role="tabpanel"
        aria-labelledby={`role-tab-${role}`}
        className="min-h-0 flex-1 overflow-hidden"
      >
        {text ? (
          <div className="min-h-0 flex-1 overflow-auto px-1 py-3">
            <PromptDocument
              key={role}
              text={text}
              compact={false}
              framed={false}
              hideHeading="身份"
              maxHeightClass="max-h-none"
            />
          </div>
        ) : (
          <p className="px-3 pt-3 text-muted-foreground text-xs">
            本角色身份未在模板中单独标出。
          </p>
        )}
      </div>
    </div>
  );
}
