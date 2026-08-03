"use client";

import { useEffect, useState } from "react";
import BrandMark from "@/components/BrandMark";
import { useLang } from "@/components/LangProvider";
import { BRAND, CTA, NAV } from "@/content/home";
import { DOWNLOAD_PAGE_PATH, WEB_APP_URL } from "@/lib/download";

/**
 * 站点顶栏。
 *
 * 三件事随滚动变化：底部 1px 进度条、导航底色由透明转实、移动端菜单。
 * 进度与底色都在同一个 rAF 节流的 scroll 回调里算，避免多处监听。
 *
 * `home={false}` 为子页形态（如下载页）：不列分区锚点，改为「返回首页」，
 * 因而也不需要汉堡菜单。
 */
export default function SiteHeader({ home = true }: { home?: boolean }) {
  const { lang, t, toggle } = useLang();
  const [progress, setProgress] = useState(0);
  const [scrolled, setScrolled] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const y = window.scrollY;
        const max = document.body.scrollHeight - window.innerHeight;
        setProgress(max > 0 ? Math.min(1, y / max) : 0);
        setScrolled(y > 60);
      });
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

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

  return (
    <>
      <header className="fixed inset-x-0 top-0 z-50">
        <div
          className="absolute inset-0 backdrop-blur-[14px] transition-[background-color,border-color] duration-350"
          style={{
            backgroundColor: scrolled
              ? "color-mix(in oklab, var(--background), transparent 18%)"
              : "transparent",
            borderBottom: `1px solid ${scrolled ? "var(--border-soft)" : "transparent"}`,
          }}
        />
        <div
          aria-hidden="true"
          className="absolute bottom-0 left-0 h-px w-full origin-left bg-gradient-to-r from-primary to-brand-2"
          style={{ transform: `scaleX(${progress})` }}
        />

        <div className="container-x relative flex h-[3.375rem] items-center justify-between gap-6 sm:h-[4.25rem]">
          <a
            href={home ? "#top" : "/"}
            className="flex items-center gap-2.5"
            onClick={() => setMenuOpen(false)}
          >
            <BrandMark size={20} />
            <span className="text-[0.9375rem] font-semibold tracking-[-0.01em] sm:text-base">
              {BRAND}
            </span>
          </a>

          {home && (
            <nav className="hidden items-center gap-[1.875rem] lg:flex">
              {NAV.map((item) => (
                <a
                  key={item.href}
                  href={item.href}
                  className="text-[0.84375rem] text-muted-foreground transition-colors hover:text-foreground"
                >
                  {t(item.label)}
                </a>
              ))}
            </nav>
          )}

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={toggle}
              aria-label={lang === "zh" ? "Switch to English" : "切换到中文"}
              className="min-h-[2.125rem] rounded-[0.4375rem] border border-border px-2.5 font-mono text-[0.6875rem] tracking-[0.08em] text-muted-foreground transition-colors hover:border-border-strong hover:text-foreground"
            >
              {lang === "zh" ? "EN" : "中文"}
            </button>

            <a
              href={home ? DOWNLOAD_PAGE_PATH : "/"}
              className="hidden px-3 py-2 text-[0.8125rem] text-muted-foreground transition-colors hover:text-foreground sm:inline-flex"
            >
              {t(home ? CTA.desktop : CTA.backHome)}
            </a>
            <a
              href={WEB_APP_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-primary hidden px-4 py-2.5 text-[0.8125rem] sm:inline-flex"
            >
              {t(CTA.webAppShort)}
            </a>

            {home && (
              <button
                type="button"
                onClick={() => setMenuOpen((v) => !v)}
                aria-expanded={menuOpen}
                aria-label={menuOpen ? "关闭菜单" : "打开菜单"}
                className="flex h-[2.125rem] w-11 flex-col items-center justify-center gap-[5px] rounded-[0.4375rem] border border-border transition-colors hover:border-border-strong lg:hidden"
              >
                <span
                  className="block h-[1.5px] w-4 rounded-sm bg-foreground/80 transition-transform duration-300"
                  style={
                    menuOpen
                      ? { transform: "translateY(3.25px) rotate(45deg)" }
                      : undefined
                  }
                />
                <span
                  className="block h-[1.5px] w-4 rounded-sm bg-foreground/80 transition-transform duration-300"
                  style={
                    menuOpen
                      ? { transform: "translateY(-3.25px) rotate(-45deg)" }
                      : undefined
                  }
                />
              </button>
            )}
          </div>
        </div>
      </header>

      {/* 移动端全屏菜单（仅首页有分区锚点可列） */}
      <div
        className={`fixed inset-x-0 bottom-0 top-[3.375rem] z-40 flex-col gap-0.5 bg-[color-mix(in_oklab,var(--background),transparent_3%)] px-[1.125rem] py-6 backdrop-blur-[20px] lg:hidden ${
          home && menuOpen ? "flex" : "hidden"
        }`}
      >
        {NAV.map((item, i) => (
          <a
            key={item.href}
            href={item.href}
            onClick={() => setMenuOpen(false)}
            className="flex min-h-14 items-center justify-between border-b border-border-soft text-[1.375rem] font-medium"
          >
            {t(item.label)}
            <span className="font-mono text-[0.6875rem] text-ghost">
              {String(i + 1).padStart(2, "0")}
            </span>
          </a>
        ))}
        <a
          href={DOWNLOAD_PAGE_PATH}
          onClick={() => setMenuOpen(false)}
          className="flex min-h-14 items-center justify-between border-b border-border-soft text-[1.375rem] font-medium"
        >
          {t(CTA.desktop)}
          <span className="font-mono text-[0.6875rem] text-ghost">
            {String(NAV.length + 1).padStart(2, "0")}
          </span>
        </a>
        <a
          href={WEB_APP_URL}
          target="_blank"
          rel="noopener noreferrer"
          onClick={() => setMenuOpen(false)}
          className="flex min-h-14 items-center justify-between text-[1.375rem] font-medium text-faint"
        >
          {t(CTA.webAppShort)}
          <span className="font-mono text-[0.6875rem] text-ghost">
            {String(NAV.length + 2).padStart(2, "0")}
          </span>
        </a>
      </div>
    </>
  );
}
