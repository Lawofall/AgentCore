import { PromptDocument } from "@/components/prompt/PromptDocument";
import { SegmentedControl } from "@/components/ui";
import { cn } from "@/lib/utils";
import { useState } from "react";

type RoleId = "ceo" | "nested" | "leaf";

const ROLES: readonly { id: RoleId; label: string }[] = [
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
      <SegmentedControl
        aria-label="角色身份"
        value={role}
        onChange={setRole}
        className="shrink-0"
        items={ROLES.map((item) => ({
          value: item.id,
          label: item.label,
          id: `role-tab-${item.id}`,
          "aria-controls": "role-identity-panel",
        }))}
      />
      <div
        id="role-identity-panel"
        role="tabpanel"
        aria-labelledby={`role-tab-${role}`}
        className="mt-3 min-h-0 flex-1 overflow-auto"
      >
        {text ? (
          <PromptDocument
            text={text}
            compact={false}
            framed={false}
            maxHeightClass="max-h-none"
          />
        ) : (
          <p className="text-muted-foreground text-xs">
            本角色身份未在模板中单独标出。
          </p>
        )}
      </div>
    </div>
  );
}
