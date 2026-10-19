import { IconButton } from "@/components/ui";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { logout } from "@/services/auth";
import { useAuthStore } from "@/stores/auth";
import { useSidebarStore } from "@/stores/sidebar";
import { useUserStore } from "@/stores/user";
import { LogOut, MoreVertical, Settings } from "lucide-react";
import { useNavigate } from "react-router-dom";

export function UserMenu() {
  const collapsed = useSidebarStore((s) => s.collapsed);
  const profile = useUserStore((s) => s.profile);
  const authUser = useAuthStore((s) => s.user);
  const navigate = useNavigate();

  const displayName =
    authUser?.displayName || authUser?.username || profile.displayName;
  const initials = displayName.charAt(0).toUpperCase();

  const handleLogout = async () => {
    try {
      await logout();
    } catch {
      /* clear the session client-side regardless of the network result */
    }
    useAuthStore.getState().setUnauthenticated();
  };

  const goSettings = () => navigate("/more");

  const avatarUrl = authUser?.avatarUrl ?? profile.avatarUrl;
  const avatar = (
    <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-sidebar-accent text-sm font-medium text-sidebar-accent-foreground">
      {avatarUrl ? (
        <img
          src={avatarUrl}
          alt={displayName}
          className="size-8 rounded-full object-cover"
        />
      ) : (
        initials
      )}
    </div>
  );

  return (
    <div className="border-t border-sidebar-border p-2">
      <div
        className={`flex items-center ${collapsed ? "justify-center" : "gap-3 px-3"}`}
      >
        {collapsed ? (
          // 折叠态：头像打开同一菜单（设置 / 登出），不另造第三入口。
          // 不用 SimpleTooltip 包 Trigger——与 DropdownMenuTrigger 双 asChild 易撞 Slot。
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label="账户菜单"
                title="账户"
                className="rounded-full p-0 outline-none focus-visible:ring-2 focus-visible:ring-sidebar-accent"
              >
                {avatar}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="right" align="end">
              <DropdownMenuItem onSelect={goSettings}>
                <Settings size={14} />
                设置
              </DropdownMenuItem>
              <DropdownMenuItem
                variant="danger"
                onSelect={() => void handleLogout()}
              >
                <LogOut size={14} />
                登出
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          <>
            {avatar}
            <span className="flex-1 truncate text-sm text-sidebar-foreground">
              {displayName}
            </span>
            <SimpleTooltip label="登出">
              <IconButton
                tone="sidebar"
                onClick={() => void handleLogout()}
                aria-label="登出"
                className="text-sidebar-foreground/50 outline-none hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-accent"
              >
                <LogOut size={14} />
              </IconButton>
            </SimpleTooltip>
            <SimpleTooltip label="设置">
              <IconButton
                tone="sidebar"
                onClick={goSettings}
                aria-label="设置"
                className="text-sidebar-foreground/50 outline-none hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-accent"
              >
                <MoreVertical size={14} />
              </IconButton>
            </SimpleTooltip>
          </>
        )}
      </div>
    </div>
  );
}
