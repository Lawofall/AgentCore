"use client";

import { useEffect, useState } from "react";
import BrandMark from "@/components/BrandMark";
import { useLang } from "@/components/LangProvider";
import { BRAND, CTA, NAV } from "@/content/home";
import { DOWNLOAD_PAGE_PATH, WEB_APP_URL } from "@/lib/download";

/**
 * 悬浮胶囊顶栏。
 *
 * 一张脱离文档流的白色圆角卡片浮在深色画布上方——这是全站唯一在暗底上
 * 直接放白块的地方，靠它把「纸」的隐喻从第一屏就立起来。
 *
 * 宽屏是三栏：左导航 + 语言、正中品牌、右主 CTA；窄屏收成品牌 + 汉堡，
 * 展开为整屏菜单（同样是白纸，与胶囊同一材质）。
 */
export default function PillNav({ home = true }: { home?: boolean }) {
  const { lang, t, toggle } = useLang();
  const [menuOpen, setMenuOpen] = useState(false);

  /* 子页上分区锚点必须补成 /#xxx——留在页内会指向不存在的元素，
     点了什么都不会发生。品牌位同理：子页应当回首页而不是滚到页顶。 */
  const anchor = (href: string) => (home ? href : `/${href}`);
  const brandHref = home ? "#top" : "/";

  // 菜单打开时锁滚动，避免背景跟着动。
  useEffect(() => {
    if (!menuOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const langToggle = (
    <button
      type="button"
      onClick={toggle}
      aria-label={lang === "zh" ? "Switch to English" : "切换到中文"}
      className="flex items-center gap-2 font-mono text-[0.6875rem] font-semibold tracking-[0.08em]"
    >
      <span className={lang === "zh" ? "text-[var(--grad-4)]" : "text-paper-faint"}>
        中
      </span>
      <span className="h-3 w-px bg-paper-line" />
      <span className={lang === "en" ? "text-[var(--grad-4)]" : "text-paper-faint"}>
        EN
      </span>
    </button>
  );

  return (
    <>
      {/* ── 宽屏：三栏胶囊 ── */}
      <header className="pointer-events-none fixed inset-x-0 top-6 z-[100] hidden justify-center px-6 lg:flex">
        <div className="pill-nav pointer-events-auto grid w-full max-w-[68rem] grid-cols-[1fr_auto_1fr] items-center gap-6 py-2.5 pl-6 pr-2.5">
          <nav className="flex items-center gap-6">
            {NAV.map((item) => (
              <a key={item.href} href={anchor(item.href)} className="pill-link">
                {t(item.label)}
              </a>
            ))}
            <span className="h-3 w-px bg-paper-line" />
            {langToggle}
          </nav>

          <a href={brandHref} className="flex items-center gap-2 justify-self-center">
            <BrandMark size={24} onLight />
            <span className="brand-word">{BRAND}</span>
          </a>

          <a
            href={WEB_APP_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-grad justify-self-end py-3 text-[0.84375rem]"
          >
            <span>{t(CTA.webApp)}</span>
            <span aria-hidden="true" className="text-[0.75rem]">
              ↗
            </span>
          </a>
        </div>
      </header>

      {/* ── 窄屏：紧凑胶囊 ── */}
      <header className="pointer-events-none fixed inset-x-0 top-3 z-[100] flex justify-center px-3 lg:hidden">
        <div className="pill-nav pointer-events-auto flex w-full items-center justify-between gap-3 py-2 pl-4 pr-2">
          <a
            href={brandHref}
            className="flex items-center gap-2"
            onClick={() => setMenuOpen(false)}
          >
            <BrandMark size={21} onLight />
            <span className="brand-word brand-word-sm">{BRAND}</span>
          </a>

          <div className="flex items-center gap-2.5">
            {langToggle}
            <button
              type="button"
              onClick={() => setMenuOpen((v) => !v)}
              aria-expanded={menuOpen}
              aria-label={menuOpen ? "关闭菜单" : "打开菜单"}
              className="flex h-9 w-10 flex-col items-center justify-center gap-[5px] rounded-[0.5rem] border border-paper-line"
            >
              <span
                className="block h-[1.5px] w-4 rounded-sm bg-paper-ink transition-transform duration-300"
                style={
                  menuOpen
                    ? { transform: "translateY(3.25px) rotate(45deg)" }
                    : undefined
                }
              />
              <span
                className="block h-[1.5px] w-4 rounded-sm bg-paper-ink transition-transform duration-300"
                style={
                  menuOpen
                    ? { transform: "translateY(-3.25px) rotate(-45deg)" }
                    : undefined
                }
              />
            </button>
          </div>
        </div>
      </header>

      {/* ── 窄屏展开菜单 ── */}
      <div
        className={`fixed inset-x-3 top-[4.25rem] z-[99] flex-col rounded-2xl bg-paper p-4 shadow-2xl lg:hidden ${
          menuOpen ? "flex" : "hidden"
        }`}
      >
        {NAV.map((item, i) => (
          <a
            key={item.href}
            href={anchor(item.href)}
            onClick={() => setMenuOpen(false)}
            className="flex min-h-14 items-center justify-between border-b border-paper-line text-[1.25rem] font-semibold text-paper-ink"
          >
            {t(item.label)}
            <span className="font-mono text-[0.6875rem] text-paper-faint">
              {String(i + 1).padStart(2, "0")}
            </span>
          </a>
        ))}
        <a
          href={DOWNLOAD_PAGE_PATH}
          onClick={() => setMenuOpen(false)}
          className="flex min-h-14 items-center justify-between text-[1.25rem] font-semibold text-paper-ink"
        >
          {t(CTA.desktop)}
          <span className="font-mono text-[0.6875rem] text-paper-faint">
            {String(NAV.length + 1).padStart(2, "0")}
          </span>
        </a>
        <a
          href={WEB_APP_URL}
          target="_blank"
          rel="noopener noreferrer"
          onClick={() => setMenuOpen(false)}
          className="btn-grad mt-3 w-full"
        >
          <span>{t(CTA.webApp)}</span>
          <span aria-hidden="true" className="text-[0.75rem]">
            ↗
          </span>
        </a>
      </div>
    </>
  );
}
