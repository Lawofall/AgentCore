import { SegmentTabs } from "@/components/ui/SegmentTabs";
import { useLocation } from "react-router-dom";

/**
 * Inner switches for the five console doors. The sidebar names the door;
 * these tabs name the room. `aria-current="true"` (not `"page"`) so they
 * don't compete with the sidebar's current-page mark.
 */

export function SupplyTabs() {
  const { pathname } = useLocation();
  return (
    <SegmentTabs
      items={[
        { to: "/quota", label: "额度", active: pathname.startsWith("/quota") },
        {
          to: "/analytics/cost",
          label: "成本",
          active: pathname.startsWith("/analytics"),
        },
      ]}
    />
  );
}

export function UserSectionTabs() {
  const { pathname } = useLocation();
  return (
    <SegmentTabs
      items={[
        {
          to: "/users",
          label: "名册",
          active:
            pathname === "/users" || pathname.startsWith("/users/"),
        },
        { to: "/audit", label: "审计", active: pathname.startsWith("/audit") },
        {
          to: "/beta-group",
          label: "内测群",
          active: pathname.startsWith("/beta-group"),
        },
      ]}
    />
  );
}

export function OpsTabs() {
  const { pathname } = useLocation();
  return (
    <SegmentTabs
      items={[
        {
          to: "/notices",
          label: "公告",
          active: pathname.startsWith("/notices"),
        },
        { to: "/store", label: "商店", active: pathname.startsWith("/store") },
      ]}
    />
  );
}
