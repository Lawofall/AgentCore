import { narrowBlockedRedirect } from "@/lib/narrowProduct";
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

  // biome-ignore lint/correctness/useExhaustiveDependencies: pathname is an intentional re-run key
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

/** Deep-link guard: narrow viewport cannot open blacklisted product surfaces. */
export function NarrowBlockedPage({
  children,
  to,
}: {
  children: ReactNode;
  to?: string;
}) {
  const isNarrow = useNarrowLayout();
  const { pathname } = useLocation();
  if (isNarrow) {
    return <Navigate to={to ?? narrowBlockedRedirect(pathname)} replace />;
  }
  return children;
}
