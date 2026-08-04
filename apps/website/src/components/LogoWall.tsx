"use client";

import Marquee from "@/components/Marquee";
import { MARQUEE, type Vendor } from "@/content/home";

/**
 * 模型厂商 logo 墙——两行反向滚动的灰度标识。
 *
 * 有 logo 文件就渲染 logo，没有就降级成字标：仓库里不存放第三方厂商的
 * 品牌资源，而厂商标识不能靠画个近似的糊过去。降级形态本身也成立——
 * 参考站那面墙上本来就有一半是字标。
 *
 * 灰度 + 压低不透明度是刻意的：这一屏的主角是「都能接」这件事，
 * 十一个彩色 logo 一起亮会把版面吵散。
 */

/*
 * 一枚厂商标识 = 图标 + 字标并排。
 *
 * 资源包只提供拆开的两件（`{brand}.svg` 图标 24×24、`{brand}-text.svg` 字标），
 * 没有合体版——所以在这里按标准锁定形态拼起来。
 *
 * 两张都不加 loading="lazy"：跑马灯的项会不断滚进视口，懒加载会让 logo
 * 在滚到眼前那一刻才闪出来。22 个 SVG 合计约 40KB，直接加载更稳。
 * 静态导出站点，不走 next/image。
 */
function VendorMark({ vendor }: { vendor: Vendor }) {
  if (vendor.hasLogo) {
    return (
      <>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={`/logos/${vendor.slug}-mark.svg`}
          alt=""
          aria-hidden="true"
          className="logo-icon"
          decoding="async"
        />
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={`/logos/${vendor.slug}-word.svg`}
          alt={vendor.name}
          className="logo-mark"
          decoding="async"
        />
      </>
    );
  }
  return <span className="logo-word">{vendor.name}</span>;
}

export default function LogoWall() {
  const { vendors, rowSplit } = MARQUEE;
  const rows = [vendors.slice(0, rowSplit), vendors.slice(rowSplit)];

  return (
    <div className="flex flex-col gap-6 md:gap-10">
      {rows.map((row, i) => (
        <Marquee
          key={i}
          items={row.map((v) => v.slug)}
          duration={i === 0 ? 46 : 38}
          gap="4.5rem"
          reverse={i === 1}
          renderItem={(slug) => {
            const vendor = row.find((v) => v.slug === slug);
            return vendor ? (
              <span className="logo-item">
                <VendorMark vendor={vendor} />
              </span>
            ) : null;
          }}
        />
      ))}
    </div>
  );
}
