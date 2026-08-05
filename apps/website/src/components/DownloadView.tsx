"use client";

import { useEffect, useMemo, useState } from "react";
import { useLang } from "@/components/LangProvider";
import Reveal from "@/components/Reveal";
import PaperFooter from "@/components/PaperFooter";
import PillNav from "@/components/PillNav";
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
      <PillNav home={false} />

      {/* 与首页同一套：main 不给背景，各分区自带不透明底；
          最后一张暗纸的下圆角要透出底下的白纸页脚。 */}
      <main className="relative z-10">
        {/* ── Hero ── */}
        <section className="relative z-0 overflow-hidden bg-[var(--ink-deep)] pb-20 pt-32 sm:pb-28 sm:pt-40">
          <div
            aria-hidden="true"
            className="starfield pointer-events-none absolute inset-0 opacity-80"
          />
          <div
            aria-hidden="true"
            className="aurora pointer-events-none absolute inset-0"
          />
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 bottom-0 h-[12.5rem] bg-gradient-to-t from-background via-background/70 to-transparent"
          />
          <div className="container-x relative z-[2]">
            <p className="float-in mb-5 inline-flex items-center gap-3 font-mono text-[0.6875rem] uppercase tracking-[0.2em] text-dim sm:mb-6">
              <span
                aria-hidden="true"
                className="block h-px w-5 shrink-0"
                style={{ background: "var(--brand-gradient)" }}
              />
              {t(HERO.eyebrow)}
            </p>
            <h1
              className="display-title float-in m-0 max-w-[47.5rem]"
              style={{ animationDelay: "80ms" }}
            >
              {t(HERO.title)}
            </h1>
            <p
              className="float-in display-lead mt-6 max-w-[36rem] text-muted-foreground"
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
                  className="btn-grad max-sm:min-h-[3.25rem] max-sm:w-full sm:px-7 sm:py-4"
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
                  className="btn-outline max-sm:min-h-[3.25rem] max-sm:w-full sm:px-7 sm:py-4"
                  style={{
                    background:
                      "linear-gradient(var(--ink-deep), var(--ink-deep)) padding-box, var(--brand-gradient) border-box",
                  }}
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
        <section
          id="platforms"
          className="panel panel-top noise panel-pad relative z-20 scroll-mt-28 bg-background"
        >
          <div className="container-x grid gap-4 sm:gap-5 lg:grid-cols-2">
            {PLATFORM_ORDER.map((id, i) => {
              const p = byId[id];
              const copy = PLATFORM_COPY[id];
              const isCurrent = detected === id;
              const ready = Boolean(p?.available && p?.url);

              return (
                <Reveal key={id} delay={i * 70} className="h-full">
                  <div
                    className={`dl-card flex h-full flex-col ${
                      isCurrent ? "is-current" : ""
                    }`}
                  >
                    <div className="mb-6 flex items-center justify-between gap-3 sm:mb-[1.875rem]">
                      <span className="font-mono text-[0.6875rem] uppercase tracking-[0.18em] text-dim">
                        {copy.label}
                      </span>
                      {isCurrent ? (
                        <span className="dl-badge">
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
                          className="dl-file"
                        >
                          <span className="min-w-0 truncate">
                            {p.fileLabel}
                          </span>
                          <span className="dl-file-action">
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
                  className="dl-quick"
                >
                  <span>
                    <span className="mb-1.5 block text-[1.0625rem] font-semibold">
                      {t(entry.title)}
                    </span>
                    <span className="block text-[0.84375rem] text-faint">
                      {t(entry.body)}
                    </span>
                  </span>
                  <span className="dl-file-action">OPEN →</span>
                </a>
              </Reveal>
            ))}
          </div>
        </section>

        {/* ── 安装步骤 + 系统要求 ── */}
        <section className="panel panel-top panel-foot noise panel-pad relative z-40 bg-[var(--ink-deep)] pb-32 sm:pb-40">
          <div
            aria-hidden="true"
            className="starfield pointer-events-none absolute inset-0 opacity-40"
          />
          <div aria-hidden="true" className="aurora-wash panel-wash z-[3]" />
          <div aria-hidden="true" className="aurora-grain panel-wash z-[4]" />
          <div className="container-x relative z-[2] grid gap-12 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:gap-[4.5rem]">
            <div>
              <Reveal>
                <p className="dl-kicker mb-7 sm:mb-9">
                  <span
                    aria-hidden="true"
                    className="block h-px w-5 shrink-0"
                    style={{ background: "var(--brand-gradient)" }}
                  />
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
                <div className="dl-panel">
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
                <div className="dl-panel mt-5">
                  <p className="mb-3.5 font-mono text-[0.65625rem] uppercase tracking-[0.18em] text-ghost">
                    {t(AUTO_UPDATE.eyebrow)}
                  </p>
                  <p className="m-0 text-sm leading-[1.75] text-dim">
                    {t(AUTO_UPDATE.body)}
                  </p>
                </div>
              </Reveal>

              <Reveal delay={160}>
                <div className="dl-panel mt-5">
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

      <PaperFooter home={false} />
    </>
  );
}
