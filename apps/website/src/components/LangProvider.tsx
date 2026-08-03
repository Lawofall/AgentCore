"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { Lang, T } from "@/content/home";

const STORAGE_KEY = "agentcore:lang";

type LangContextValue = {
  lang: Lang;
  /** 取当前语言下的文案。 */
  t: (value: T) => string;
  toggle: () => void;
};

const LangContext = createContext<LangContextValue | null>(null);

/**
 * 站点语言（zh / en）。
 *
 * 站点是静态导出（`output: "export"`），没有服务端协商，因此首屏一律按
 * 中文渲染，挂载后再读 localStorage 切换——避免 hydration 前后文案不一致。
 */
export default function LangProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [lang, setLang] = useState<Lang>("zh");

  useEffect(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "en" || saved === "zh") setLang(saved);
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
  }, [lang]);

  const toggle = useCallback(() => {
    setLang((prev) => {
      const next = prev === "zh" ? "en" : "zh";
      window.localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

  const value = useMemo<LangContextValue>(
    () => ({ lang, toggle, t: (v: T) => v[lang] }),
    [lang, toggle],
  );

  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useLang(): LangContextValue {
  const ctx = useContext(LangContext);
  if (!ctx) throw new Error("useLang 必须在 <LangProvider> 内使用");
  return ctx;
}
