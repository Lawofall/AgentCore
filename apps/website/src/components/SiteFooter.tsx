"use client";

import BrandMark from "@/components/BrandMark";
import { useLang } from "@/components/LangProvider";
import { BRAND, CTA, FOOTER } from "@/content/home";
import {
  DOWNLOAD_PAGE_PATH,
  MOBILE_WEB_URL,
  WEB_APP_URL,
} from "@/lib/download";

/**
 * 暗色页脚（子页形态，如下载页）。
 * 首页用的是白纸大字标页脚，见 components/PaperFooter.tsx。
 */
export default function SiteFooter({ home = true }: { home?: boolean }) {
  const { t } = useLang();
  const anchor = (href: string) => (home ? href : `/${href}`);

  const linkClass =
    "text-[0.84375rem] text-muted-foreground transition-colors hover:text-foreground";

  return (
    <footer className="relative z-[1] border-t border-border-soft pb-10 pt-16">
      <div className="container-x">
        <div className="mb-14 grid gap-10 sm:grid-cols-2 lg:grid-cols-[2fr_1fr_1fr_1fr]">
          <div>
            <div className="mb-4 flex items-center gap-2.5">
              <BrandMark size={20} />
              <span className="text-[0.9375rem] font-semibold">{BRAND}</span>
            </div>
            <p className="m-0 max-w-80 text-[0.84375rem] leading-[1.75] text-ghost">
              {t(FOOTER.blurb)}
            </p>
          </div>

          <div>
            <p className="mb-[1.125rem] font-mono text-[0.65625rem] uppercase tracking-[0.16em] text-ghost">
              {t(FOOTER.colProduct)}
            </p>
            <div className="flex flex-col gap-[0.6875rem]">
              <a
                href={WEB_APP_URL}
                target="_blank"
                rel="noopener noreferrer"
                className={linkClass}
              >
                {t(CTA.webAppShort)}
              </a>
              <a href={DOWNLOAD_PAGE_PATH} className={linkClass}>
                {t(CTA.desktop)}
              </a>
              <a
                href={MOBILE_WEB_URL}
                target="_blank"
                rel="noopener noreferrer"
                className={linkClass}
              >
                {t(CTA.mobileWeb)}
              </a>
            </div>
          </div>

          <div>
            <p className="mb-[1.125rem] font-mono text-[0.65625rem] uppercase tracking-[0.16em] text-ghost">
              {t(FOOTER.colLearn)}
            </p>
            <div className="flex flex-col gap-[0.6875rem]">
              {FOOTER.learn.map((l) => (
                <a key={l.href} href={anchor(l.href)} className={linkClass}>
                  {t(l.label)}
                </a>
              ))}
            </div>
          </div>

          <div>
            <p className="mb-[1.125rem] font-mono text-[0.65625rem] uppercase tracking-[0.16em] text-ghost">
              {t(FOOTER.colAbout)}
            </p>
            <div className="flex flex-col gap-[0.6875rem]">
              {FOOTER.about.map((l) => (
                <a key={l.href} href={anchor(l.href)} className={linkClass}>
                  {t(l.label)}
                </a>
              ))}
              <span className="text-[0.84375rem] text-ghost">
                {t(FOOTER.aboutNote)}
              </span>
            </div>
          </div>
        </div>

        <div className="flex flex-col gap-3 border-t border-border-soft pt-[1.625rem] font-mono text-[0.6875rem] text-[var(--ghost)] sm:flex-row sm:items-center sm:justify-between">
          <span>{t(FOOTER.copyright)}</span>
          <span>{FOOTER.stack}</span>
        </div>
      </div>
    </footer>
  );
}
