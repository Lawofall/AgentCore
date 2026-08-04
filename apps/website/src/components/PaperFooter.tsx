"use client";

import { useLang } from "@/components/LangProvider";
import { BRAND, CTA, FOOTER } from "@/content/home";
import {
  DOWNLOAD_PAGE_PATH,
  MOBILE_WEB_URL,
  WEB_APP_URL,
} from "@/lib/download";

/**
 * 首页白纸页脚。
 *
 * 三层叠出来的收尾：
 *   z-10  完整的巨型字标（深墨色，不裁切）
 *   z-20  一条 blur(64px) 的六段彩虹压在字标下缘 —— 颜色是「洗」上去的，不是渐变字
 *   z-21  噪点；z-22 白色淡出，把最底部收干净
 *
 * 宽屏下整块 fixed 在视口底部、z 轴沉到内容之下，靠上面的内容滚走来揭开；
 * 窄屏回到常规文档流（fixed 幕布在移动端容易和地址栏高度打架）。
 */
export default function PaperFooter({ home = true }: { home?: boolean }) {
  const { t } = useLang();
  /** 子页上把页内锚点补成 /#xxx，否则点了没有反应。 */
  const anchor = (href: string) => (home ? href : `/${href}`);

  return (
    <footer className="paper-curtain relative w-full">
      {/* 不要在这里加 Tailwind 的 relative/fixed——工具类层级晚于 components，
          会压过 .paper-curtain-inner 在断点里切换到 fixed 的规则。 */}
      <div className="paper-curtain-inner flex w-full flex-col overflow-hidden bg-paper">
        {/* 底纹：24px 网格圆点 */}
        <div aria-hidden="true" className="footer-dots absolute inset-0 z-0" />
        {/* 噪点，顶缘淡入 */}
        <div
          aria-hidden="true"
          className="footer-grain absolute inset-0 z-0 opacity-[0.05]"
          style={{
            maskImage: "linear-gradient(to bottom, transparent, black 15%)",
          }}
        />

        {/* 链接区 */}
        <div className="relative z-30 flex flex-1 flex-col px-6 pb-0 pt-24 md:px-12 md:pt-32">
          {/* 三列在左侧紧凑排布（gap-14），不是两端对齐——照 amphora 的排法。 */}
          <div className="flex flex-col gap-8 sm:flex-row sm:items-start sm:gap-14">
            <div className="flex flex-col items-start gap-5 md:gap-7">
              <a
                href={WEB_APP_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="footer-link"
              >
                {t(CTA.webAppShort)}
              </a>
              <a href={DOWNLOAD_PAGE_PATH} className="footer-link">
                {t(CTA.desktop)}
              </a>
              <a
                href={MOBILE_WEB_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="footer-link"
              >
                {t(CTA.mobileWeb)}
              </a>
            </div>

            <div className="flex flex-col items-start gap-5 md:gap-7">
              {FOOTER.learn.map((l) => (
                <a key={l.href} href={anchor(l.href)} className="footer-link">
                  {t(l.label)}
                </a>
              ))}
            </div>

            <div className="flex flex-col items-start gap-3.5">
              <span className="footer-link footer-link-static">
                {t(FOOTER.colAbout)}
              </span>
              {FOOTER.about.map((l) => (
                <a
                  key={l.href}
                  href={anchor(l.href)}
                  className="footer-sublink text-[0.9375rem] font-medium"
                >
                  {t(l.label)}
                </a>
              ))}
              <p className="m-0 max-w-[20rem] text-[0.875rem] leading-[1.7] text-paper-faint">
                {t(FOOTER.blurb)}
              </p>
            </div>
          </div>
        </div>

        {/* 版权 + 字标 */}
        <div className="pointer-events-none relative z-10 mt-14 flex flex-col md:absolute md:inset-x-0 md:bottom-4 md:mt-0">
          <p className="pointer-events-auto z-40 m-0 mb-3 px-6 text-right text-base font-normal text-paper-ink md:px-16 md:text-lg">
            {t(FOOTER.copyright)}
          </p>
          <div className="flex justify-center">
            <span
              aria-hidden="true"
              className="footer-wordmark select-none whitespace-nowrap font-bold leading-[0.78] tracking-[-0.045em] text-paper-ink"
            >
              {BRAND}
            </span>
          </div>
        </div>

        {/* 彩虹「洗色」：与上面那张暗纸下缘共用 .aurora-wash / .aurora-grain，
            接缝两侧同一条光、同一层噪点。
            宽屏压在字标之上（z-20 > 字标的 z-10），带高约等于字标 ink 高度的六成——
            上缘留住墨色，下缘才被洗掉；窄屏字本来就矮，改成沉到字标背后当光晕。 */}
        <div
          aria-hidden="true"
          className="aurora-wash footer-glow -bottom-4 z-0 md:bottom-0 md:z-20"
        />
        <div
          aria-hidden="true"
          className="aurora-grain footer-glow -bottom-4 z-[1] md:bottom-0 md:z-[21]"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-0 bottom-0 z-[2] h-[200px] md:z-[22]"
          style={{
            background:
              "linear-gradient(to top, rgb(255 255 255 / 0.55) 0%, rgb(255 255 255 / 0.15) 60%, transparent 100%)",
          }}
        />
      </div>
    </footer>
  );
}
