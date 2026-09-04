import { PromptDocument } from "@/components/prompt/PromptDocument";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { useState } from "react";

type RoleId = "ceo" | "nested" | "leaf";

const ROLES: readonly {
  id: RoleId;
  label: string;
  caption: string;
}[] = [
  {
    id: "ceo",
    label: "主 Agent",
    caption: "用户只跟你说话，对整段对话负责。",
  },
  {
    id: "nested",
    label: "可再委派的队员",
    caption: "对节点交差，还可再带一层子队。",
  },
  {
    id: "leaf",
    label: "叶子队员",
    caption: "对节点交差，不能再向下委派。",
  },
];

/** Mutually exclusive role `<身份>` switcher — one tab at a time, never stacked. */
export function RoleIdentityBlock({
  ceoIdentity,
  nestedIdentity,
  leafIdentity,
}: {
  ceoIdentity: string;
  nestedIdentity: string;
  leafIdentity: string;
}) {
  const [role, setRole] = useState<RoleId>("ceo");
  const selected = ROLES.find((item) => item.id === role) ?? ROLES[0];
  const text =
    role === "ceo"
      ? ceoIdentity
      : role === "nested"
        ? nestedIdentity
        : leafIdentity;

  return (
    <div>
      <div
        role="tablist"
        aria-label="角色身份"
        className="scrollbar-hidden flex min-w-0 overflow-x-auto rounded-lg border border-border p-0.5"
      >
        {ROLES.map((item) => {
          const active = item.id === role;
          return (
            <Button
              key={item.id}
              variant="ghost"
              role="tab"
              aria-selected={active}
              aria-controls="role-identity-panel"
              id={`role-tab-${item.id}`}
              onClick={() => setRole(item.id)}
              className={cn(
                "h-8 min-w-0 flex-1 shrink-0 rounded-lg px-3 text-sm",
                active
                  ? "bg-accent text-foreground hover:bg-accent"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {item.label}
            </Button>
          );
        })}
      </div>
      <p className="mt-2 text-muted-foreground text-xs">{selected.caption}</p>
      <div
        id="role-identity-panel"
        role="tabpanel"
        aria-labelledby={`role-tab-${role}`}
        className="mt-3"
      >
        {text ? (
          <PromptDocument
            text={text}
            compact={false}
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
