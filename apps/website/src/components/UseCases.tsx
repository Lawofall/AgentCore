"use client";

import { useLang } from "@/components/LangProvider";
import Reveal from "@/components/Reveal";
import { USECASES } from "@/content/home";
import { WEB_APP_URL } from "@/lib/download";

/**
 * 「能交付什么」四张图文卡，左右交替。
 *
 * 图片走 public/usecases/{key}.jpg，上面压一层该行主色——原图直出会各自
 * 跑色（冷调手机、暖调毕业照），四张卡摆在一起就散了。
 *
 * 每行主色取招牌渐变的一个色停（--grad-1..4），图、图标、勾选、按钮、辉光
 * 全部由同一个变量驱动，换色只改一处。
 */

const ICONS: Record<string, React.ReactNode> = {
  app: (
    <g strokeLinecap="round" strokeLinejoin="round">
      <rect x="7" y="2.5" width="10" height="19" rx="2.5" />
      <path d="M10.5 5.5h3" />
    </g>
  ),
  site: (
    <g strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18" />
    </g>
  ),
  deck: (
    <g strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M12 16v4M8.5 20h7" />
    </g>
  ),
  paper: (
    <g strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 2.5h8l4 4v15H6z" />
      <path d="M14 2.5v4h4M9 12h6M9 16h6" />
    </g>
  ),
};

export default function UseCases() {
  const { t } = useLang();

  return (
    <div className="flex flex-col gap-4 sm:gap-5">
      {USECASES.cases.map((item, i) => {
        // 偶数行图在左，奇数行图在右——靠 order 翻，DOM 顺序保持「图先文后」，
        // 窄屏塌成单列时才不会出现「先读完文案再看到图」。
        const mediaRight = i % 2 === 1;

        return (
          <Reveal key={item.key} delay={i * 60}>
            {/* 整张卡就是链接：右上角那个 ↗ 本来就在暗示「这块能点」，
                只做 hover 动效却不可点会别扭。卡内因此不能再嵌 <a>，
                原来的 CTA 降级成 <span>，样式不变。 */}
            <a
              href={WEB_APP_URL}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`${t(item.title)} — ${t(item.cta)}`}
              className="uc-card"
              style={{ ["--uc" as string]: `var(--grad-${item.accent})` }}
            >
              <div
                className={`uc-media ${mediaRight ? "md:order-2" : "md:order-1"}`}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={`/usecases/${item.key}.jpg`}
                  alt=""
                  aria-hidden="true"
                  className="uc-photo"
                  loading="lazy"
                  decoding="async"
                />
                {/* 主色调压一层：原图直出会各自跑色，四张卡就散了。 */}
                <span className="uc-tint" aria-hidden="true" />
              </div>

              <div
                className={`uc-body ${mediaRight ? "md:order-1" : "md:order-2"}`}
              >
                <span className="uc-corner" aria-hidden="true">
                  ↗
                </span>

                <span className="uc-icon" aria-hidden="true">
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                  >
                    {ICONS[item.key]}
                  </svg>
                </span>

                <p className="uc-eyebrow">{t(item.eyebrow)}</p>
                <h3 className="uc-title">{t(item.title)}</h3>
                <p className="uc-desc">{t(item.body)}</p>

                <ul className="uc-list">
                  {item.bullets.map((b) => (
                    <li key={b.zh}>
                      <span className="uc-check" aria-hidden="true">
                        <svg viewBox="0 0 24 24" fill="none">
                          <path
                            d="m7 12.5 3.2 3.2L17 9"
                            stroke="currentColor"
                            strokeWidth="2.4"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      </span>
                      {t(b)}
                    </li>
                  ))}
                </ul>

                <span className="uc-cta">
                  {t(item.cta)}
                  <span aria-hidden="true">↗</span>
                </span>
              </div>
            </a>
          </Reveal>
        );
      })}
    </div>
  );
}
