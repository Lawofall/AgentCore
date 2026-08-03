"use client";

import { useEffect, useMemo, useState } from "react";
import { useLang } from "@/components/LangProvider";
import Reveal from "@/components/Reveal";
import SiteFooter from "@/components/SiteFooter";
import SiteHeader from "@/components/SiteHeader";
import {
  AUTO_UPDATE,
  HERO,
  INSTALL,
  INSTALL_STEPS,
  LABELS,
  PLATFORM_COPY,
  PLATFORM_ORDER,
  QUICK_ENTRIES,
  RELEASE,
  REQUIREMENTS,
} from "@/content/download";
import { useDesktopRelease } from "@/hooks/useDesktopRelease";
import {
  MOBILE_WEB_URL,
  RELEASES_LATEST,
  WEB_APP_URL,
  platformsFromArtifacts,
  type PlatformId,
} from "@/lib/download";

/** UA 嗅探只用于「把哪张卡标成当前设备」，不影响任何下载直链。 */
function detectPlatform(): PlatformId {
  if (typeof navigator === "undefined") return "win";
  const ua = navigator.userAgent.toLowerCase();
  // Android 的 UA 里也含 "linux"，必须先判 android。
  if (ua.includes("android")) return "android";
  if (ua.includes("mac")) return "mac";
  if (ua.includes("linux")) return "linux";
  return "win";
}

function DownloadIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M8 1.8v8.2m0 0L4.6 6.6M8 10 11.4 6.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M2.6 10.8v2.2a1 1 0 0 0 1 1h8.8a1 1 0 0 0 1-1v-2.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function DownloadView() {
  const { t } = useLang();
  const { artifacts } = useDesktopRelease();

  // 首屏一律按 win 渲染，挂载后再嗅探——静态导出没有服务端可协商。
  const [detected, setDetected] = useState<PlatformId | null>(null);
  useEffect(() => setDetected(detectPlatform()), []);

  const platforms = useMemo(
    () => platformsFromArtifacts(artifacts),
    [artifacts],
  );
  const byId = useMemo(
    () => Object.fromEntries(platforms.map((p) => [p.id, p])),
    [platforms],
  ) as Record<PlatformId, (typeof platforms)[number]>;

  const primary = byId[detected ?? "win"];
  const primaryReady = Boolean(primary?.available && primary?.url);

  return (
    <>
      <div aria-hidden="true" className="grid-backdrop">
        <span />
      </div>

      <SiteHeader home={false} />

      <main>
        {/* ── Hero ── */}
        <section className="relative z-[1] overflow-hidden pb-14 pt-[6.5rem] sm:pb-[4.75rem] sm:pt-[9.375rem]">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute -top-32 left-1/2 h-[32.5rem] w-[min(61.25rem,100vw)] -translate-x-1/2"
            style={{
              background:
                "radial-gradient(ellipse at center, var(--glow-1), transparent 62%)",
            }}
          />
          <div className="container-x relative">
            <p className="eyebrow float-in mb-5 sm:mb-[1.625rem]">
              {t(HERO.eyebrow)}
            </p>
            <h1
              className="float-in m-0 max-w-[47.5rem] text-[clamp(2.25rem,4.4vw,3.75rem)] font-semibold leading-[1.1] tracking-[-0.035em] [text-wrap:pretty]"
              style={{ animationDelay: "80ms" }}
            >
              {t(HERO.title)}
            </h1>
            <p
              className="float-in mt-5 max-w-[35rem] text-[0.96875rem] leading-[1.75] text-muted-foreground [text-wrap:pretty] sm:mt-6 sm:text-[1.0625rem]"
              style={{ animationDelay: "160ms" }}
            >
              {t(HERO.lead)}
            </p>

            <div
              className="float-in mt-8 flex flex-col items-start gap-3.5 sm:mt-10 sm:flex-row sm:flex-wrap sm:items-center"
              style={{ animationDelay: "240ms" }}
            >
              {primaryReady ? (
                <a
                  href={primary.url}
                  className="btn btn-primary max-sm:w-full max-sm:min-h-[3.25rem] sm:px-[1.625rem] sm:py-4 sm:text-[0.9375rem]"
                >
                  <DownloadIcon />
                  {t(HERO.cta)}
                  {/* 只印平台，不印版本：同一次发布里各平台产物版本可能不一致
                      （例如 mac 落后一版），旁边的真实文件名才是准确版本。 */}
                  <span className="font-mono text-[0.6875rem] opacity-60">
                    {detected
                      ? PLATFORM_COPY[detected].label.toUpperCase()
                      : t(HERO.detecting)}
                  </span>
                </a>
              ) : (
                <a
                  href="#platforms"
                  className="btn btn-ghost max-sm:w-full max-sm:min-h-[3.25rem] sm:px-[1.625rem] sm:py-4 sm:text-[0.9375rem]"
                >
                  {t(LABELS.allPlatforms)}
                </a>
              )}
              <span className="font-mono text-[0.6875rem] tracking-[0.08em] text-ghost">
                {primaryReady && primary.fileLabel
                  ? primary.fileLabel
                  : `v${artifacts.version}`}
              </span>
            </div>
          </div>
        </section>

        {/* ── 平台卡 ── */}
        <section id="platforms" className="relative z-[1] pb-16 sm:pb-[7.5rem]">
          <div className="container-x grid gap-4 sm:gap-5 lg:grid-cols-2">
            {PLATFORM_ORDER.map((id, i) => {
              const p = byId[id];
              const copy = PLATFORM_COPY[id];
              const isCurrent = detected === id;
              const ready = Boolean(p?.available && p?.url);

              return (
                <Reveal key={id} delay={i * 70} className="h-full">
                  <div
                    className={`flex h-full flex-col rounded-[1.125rem] px-6 pb-[1.875rem] pt-[1.75rem] sm:px-8 sm:pt-[2.125rem] ${
                      isCurrent
                        ? "surface-accent"
                        : "border border-border-soft bg-[var(--panel)]"
                    }`}
                  >
                    <div className="mb-6 flex items-center justify-between gap-3 sm:mb-[1.875rem]">
                      <span className="font-mono text-[0.6875rem] uppercase tracking-[0.18em] text-dim">
                        {copy.label}
                      </span>
                      {isCurrent ? (
                        <span className="rounded-[1.25rem] bg-primary px-2.5 py-1 font-mono text-[0.625rem] tracking-[0.14em] text-primary-foreground">
                          {t(LABELS.currentDevice)}
                        </span>
                      ) : !p?.available ? (
                        <span className="rounded-[1.25rem] border border-border px-2.5 py-1 font-mono text-[0.625rem] tracking-[0.14em] text-ghost">
                          {t(LABELS.comingSoon)}
                        </span>
                      ) : null}
                    </div>

                    <p className="m-0 mb-2 text-[1.3125rem] font-semibold tracking-[-0.02em] sm:text-2xl">
                      {t(copy.subtitle)}
                    </p>
                    <p className="m-0 mb-7 text-sm leading-[1.7] text-faint sm:mb-[1.875rem]">
                      {t(copy.meta)}
                    </p>

                    <div className="mt-auto">
                      {ready ? (
                        <a
                          href={p.url}
                          className="flex items-center justify-between gap-4 rounded-[0.6875rem] border border-border-strong px-[1.125rem] py-[0.9375rem] text-[0.90625rem] transition-colors hover:border-[color-mix(in_oklab,var(--primary),transparent_55%)]"
                        >
                          <span className="min-w-0 truncate">
                            {p.fileLabel}
                          </span>
                          <span className="shrink-0 font-mono text-[0.6875rem] text-primary">
                            {t(LABELS.download)} ↓
                          </span>
                        </a>
                      ) : (
                        <p className="m-0 rounded-[0.6875rem] border border-border px-[1.125rem] py-[0.9375rem] text-[0.84375rem] text-ghost">
                          {t(
                            p?.id === "linux"
                              ? LABELS.comingSoon
                              : LABELS.notReleased,
                          )}
                        </p>
                      )}
                    </div>
                  </div>
                </Reveal>
              );
            })}
          </div>

          {/* 免安装入口 */}
          <div className="container-x mt-4 grid gap-4 sm:mt-5 sm:gap-5 lg:grid-cols-2">
            {QUICK_ENTRIES.map((entry, i) => (
              <Reveal key={entry.key} delay={i * 70}>
                <a
                  href={entry.key === "web" ? WEB_APP_URL : MOBILE_WEB_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-between gap-5 rounded-2xl border border-border-soft bg-[var(--panel)] px-6 py-6 transition-colors hover:border-[color-mix(in_oklab,var(--primary),transparent_62%)] sm:px-7"
                >
                  <span>
                    <span className="mb-1.5 block text-[1.0625rem] font-semibold">
                      {t(entry.title)}
                    </span>
                    <span className="block text-[0.84375rem] text-faint">
                      {t(entry.body)}
                    </span>
                  </span>
                  <span className="shrink-0 font-mono text-[0.6875rem] text-primary">
                    OPEN →
                  </span>
                </a>
              </Reveal>
            ))}
          </div>
        </section>

        {/* ── 安装步骤 + 系统要求 ── */}
        <section className="section">
          <div className="container-x grid gap-12 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:gap-[4.5rem]">
            <div>
              <Reveal>
                <p className="eyebrow mb-7 sm:mb-[2.125rem]">
                  {t(INSTALL.eyebrow)}
                </p>
              </Reveal>

              {PLATFORM_ORDER.filter(
                (id) => INSTALL_STEPS[id] && byId[id]?.available,
              ).map((id) => (
                <Reveal key={id}>
                  <div className="mb-8 last:mb-0">
                    <p className="mb-3.5 flex items-baseline gap-3 text-[1.0625rem] font-semibold">
                      {PLATFORM_COPY[id].label}
                      <span className="font-mono text-[0.6875rem] font-normal text-ghost">
                        {t(PLATFORM_COPY[id].subtitle)}
                      </span>
                    </p>
                    <ol className="m-0 list-none p-0">
                      {INSTALL_STEPS[id]?.map((step, i) => (
                        <li
                          key={step.zh}
                          className="flex gap-[1.125rem] border-t border-border-soft py-[1.125rem] last:border-b"
                        >
                          <span className="shrink-0 basis-6 pt-0.5 font-mono text-[0.625rem] tracking-[0.12em] text-primary">
                            {String(i + 1).padStart(2, "0")}
                          </span>
                          <span className="text-[0.90625rem] leading-[1.7] text-muted-foreground">
                            {t(step)}
                          </span>
                        </li>
                      ))}
                    </ol>
                  </div>
                </Reveal>
              ))}
            </div>

            <div>
              <Reveal>
                <div className="rounded-2xl border border-border-soft bg-[var(--panel)] px-7 py-[1.875rem]">
                  <p className="mb-6 font-mono text-[0.65625rem] uppercase tracking-[0.18em] text-ghost">
                    {t(REQUIREMENTS.eyebrow)}
                  </p>
                  <dl className="m-0 flex flex-col gap-4 text-sm leading-[1.6]">
                    {REQUIREMENTS.rows.map((row) => (
                      <div
                        key={row.k.zh}
                        className="flex justify-between gap-4"
                      >
                        <dt className="text-faint">{t(row.k)}</dt>
                        <dd className="m-0 text-right text-foreground/85">
                          {t(row.v)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </Reveal>

              <Reveal delay={80}>
                <div className="mt-5 rounded-2xl border border-border-soft bg-[var(--panel)] px-7 py-6">
                  <p className="mb-3.5 font-mono text-[0.65625rem] uppercase tracking-[0.18em] text-ghost">
                    {t(AUTO_UPDATE.eyebrow)}
                  </p>
                  <p className="m-0 text-sm leading-[1.75] text-dim">
                    {t(AUTO_UPDATE.body)}
                  </p>
                </div>
              </Reveal>

              <Reveal delay={160}>
                <div className="mt-5 rounded-2xl border border-border-soft bg-[var(--panel)] px-7 py-6">
                  <p className="mb-3.5 font-mono text-[0.65625rem] uppercase tracking-[0.18em] text-ghost">
                    {t(RELEASE.eyebrow)}
                  </p>
                  <div className="flex flex-col gap-2.5 text-sm">
                    <a
                      href={artifacts.releaseNotesUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-muted-foreground transition-colors hover:text-foreground"
                    >
                      {t(RELEASE.notes)} · v{artifacts.version} →
                    </a>
                    <a
                      href={RELEASES_LATEST}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-muted-foreground transition-colors hover:text-foreground"
                    >
                      {t(RELEASE.history)} →
                    </a>
                  </div>
                </div>
              </Reveal>
            </div>
          </div>
        </section>
      </main>

      <SiteFooter home={false} />
    </>
  );
}
