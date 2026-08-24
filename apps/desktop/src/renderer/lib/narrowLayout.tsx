import {
  isNarrowBlockedPath,
  narrowBlockedRedirect,
} from "@/lib/narrowProduct";
import { shouldHideNarrowChrome, useNarrowLayout } from "@/lib/useNarrowLayout";
import {
  type ReactNode,
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { Navigate, useLocation } from "react-router-dom";

export interface NarrowLayoutState {
  isNarrow: boolean;
  hideChrome: boolean;
  conversationDrawerOpen: boolean;
  setConversationDrawerOpen: (open: boolean) => void;
}

const NarrowLayoutContext = createContext<NarrowLayoutState | null>(null);

export function NarrowLayoutProvider({ children }: { children: ReactNode }) {
  const isNarrow = useNarrowLayout();
  const { pathname } = useLocation();
  const hideChrome = isNarrow && shouldHideNarrowChrome(pathname);
  const [conversationDrawerOpen, setConversationDrawerOpen] = useState(false);

  useEffect(() => {
    if (!isNarrow) setConversationDrawerOpen(false);
  }, [isNarrow]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: pathname 是换路由触发键，不是 effect 体内读取的值。
  useEffect(() => {
    setConversationDrawerOpen(false);
  }, [pathname]);

  const value = useMemo(
    () => ({
      isNarrow,
      hideChrome,
      conversationDrawerOpen,
      setConversationDrawerOpen,
    }),
    [isNarrow, hideChrome, conversationDrawerOpen],
  );

  return (
    <NarrowLayoutContext.Provider value={value}>
      {children}
    </NarrowLayoutContext.Provider>
  );
}

const WIDE_FALLBACK: NarrowLayoutState = {
  isNarrow: false,
  hideChrome: false,
  conversationDrawerOpen: false,
  setConversationDrawerOpen: () => undefined,
};

export function useNarrowLayoutState(): NarrowLayoutState {
  return useContext(NarrowLayoutContext) ?? WIDE_FALLBACK;
}

/**
 * 窄屏黑名单路由：宽屏原样渲染；窄屏落到对话或设置列表。
 * 权威 → 前端技术 §五 / {@link isNarrowBlockedPath}。
 */
export function NarrowBlockedPage({ children }: { children: ReactNode }) {
  const { isNarrow } = useNarrowLayoutState();
  const { pathname } = useLocation();
  if (isNarrow && isNarrowBlockedPath(pathname)) {
    return <Navigate to={narrowBlockedRedirect(pathname)} replace />;
  }
  return <>{children}</>;
}
