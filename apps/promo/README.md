# apps/promo — AgentCore 宣传内容

> **双轨定界**：Remotion 合成与真机捕获素材是两条**并行产线**；成片靠外部剪辑合成。本仓库**不做** Remotion 消费真机素材的闭环，也**不**把 `assets/` 迁出 promo。
>
> **与 demos 的边界**：磁带回放 / 演示磁带属 [`demos/`](/demos/README.md)；宣传静帧、短片与 Remotion 成片属本目录。

本 README 是宣传内容的现状说明书，分三条产线。

## 版权与素材

- Remotion 品牌片与代码：随仓库许可（[FSL-1.1-ALv2](/LICENSE)）。
- 内嵌字体：Inter / Noto Sans SC，见 [`src/core/fonts/NOTICE.md`](./src/core/fonts/NOTICE.md)（SIL OFL 1.1）。
- 真机捕获静帧/短片：**不入公开仓**（见 `assets/lv-molihua/MANIFEST.md`）；本地宣传制作请自备素材。片中出现的第三方商标归各权利人所有，仅作产品演示语境。
- BGM：不入库，成片由剪辑侧自行添加。

---

## 一、品牌片 · brand-30s（Remotion）

30 秒横屏品牌片：纯 Remotion 复用 desktop 真组件渲染成片（「全 B」路线）。画面、节奏、组件清单以 `src/videos/brand-30s/` 代码为准。

### 定档事实

① 30 秒**横屏**（16:9）；② 风格＝**真实产品演示**，与产品像素一致；③ 技术路线＝**纯 Remotion**；④ **亮色** + **桌面端外壳**；⑤ 字幕由本片产出、**BGM 用户后续自加**、无旁白；⑥ 片尾**无 CTA**（仅 Logo + slogan）；⑦ slogan＝「**AgentCore · 协作，是更高级的智能**」；⑧ demo＝5 层 DAG + 多方圆桌（11 Worker）。

### 为何选 Remotion（决策摘要）

| 方案 | 结论 |
|---|---|
| A · 截真机（Playwright） | 真实性最硬，但帧级卡点弱、改版贵 → **否决为成片主路**；截图仅作像素标尺（`PixelCheck`） |
| **B · Remotion 复用真组件** ✅ | 帧级时间轴 + 改 prop 重渲；chrome 脚手架重建、运动从帧钟重驱 |

像素同源要点：共享 desktop `globals.css` + 叶子组件直复用 + chrome 脚手架 + ELK 布局预计算 + 内嵌 Inter / Noto Sans SC。→ 见代码: `src/core/`、`src/videos/brand-30s/`。

### 工程结构

- `src/core/` —— 通用引擎（**禁止** import `videos/`）
- `src/videos/brand-30s/` —— 30s 品牌片（`timeline` / `manifest` / `Video` / `scenes/` / `data/`）
- `src/videos/lv-molihua/` —— 热点片相关 Remotion composition（片头/章节/静帧壳；**不**直接吃 `assets/` 真机 webm）
- `src/stills/` —— 与视频包平行的素材包（`pnpm stills` → `out/stills/`）
- `scripts/` —— ELK 预计算与渲染

### 渲染 / 预览

在 `apps/promo` 内（以 `package.json` 为准）：

```bash
pnpm dev     # Remotion Studio
pnpm build   # 成片 → out/promo.mp4
pnpm still   # 像素核对静帧 → out/pixel-check.png
pnpm stills  # Still 套件 → out/stills/
```

BGM：见 [`public/README.md`](./public/README.md)。

---

## 二、热点片 · lv-molihua（真机素材仓）

「LV 诉茉莉奶白」多模型辩论宣传素材：由桌面端 Playwright 捕获脚本在**生产 webapp + demo-tape 导演台**上回放磁带，产出静帧 / 短片。磁带与回放管线见 [`demos/README.md`](/demos/README.md)；本目录只收**宣传交付物**与捕获说明。

素材树：`assets/lv-molihua/`（清单 [`MANIFEST.md`](./assets/lv-molihua/MANIFEST.md) / [`manifest.json`](./assets/lv-molihua/manifest.json)）。

### `assets/<项目>/` 目录分层

分层靠 **gitignore + 本文档**表达；捕获脚本输出路径为 `_video_tmp*` / `sequences/` / `stills/` / `clips/`（与下表一致）。

| 层 | 典型路径 | 入仓？ | 语义 |
|---|---|---|---|
| 临时原始录屏 | `_video_tmp/`、`_video_tmp_speed1/` 等 | **否**（`apps/promo/.gitignore`） | Playwright `recordVideo` 原始 webm，可重复生成 |
| 中间帧序列 | `sequences/` | **否** | 抽帧 / 推进序列等中间产物 |
| 精选交付物 | `stills/`、`clips/` | **是** | 分镜静帧与精选短片；以 MANIFEST 为登记真相源 |
| 元数据 | `MANIFEST.md`、`manifest.json`、验收 JSON | **是** | 镜头目录、导演台验收、捕获报告 |

### Canonical 捕获命令

前提：后端 `DEMO_TAPE_REPLAY_ENABLED=true`（建议 `:8015`）；桌面已 `pnpm build:webapp`。入口在 `apps/desktop`（Playwright 依赖所在）：

```powershell
cd apps/desktop
$env:VITE_API_URL='http://localhost:8015'
pnpm build:webapp
$env:PROMO_API='http://localhost:8015'
$env:PROMO_USER='promo_lv'
$env:PROMO_PASS='promopass'
pnpm promo:lv:full
# 等价：node scripts/promo_capture_lv_molihua.mjs full
```

**Canonical**＝`node scripts/promo_capture_lv_molihua.mjs full`（干净环境 + 导演台 seek/变速/验收，默认写出 `apps/promo/assets/lv-molihua/`）。`pnpm promo:lv -- --help` 列出子命令与参数。

注意：`PROMO_API` 与构建时 `VITE_API_URL` 必须同为 `localhost`（勿混用 `127.0.0.1`——cookie 按 host 判同站，混用会 401）；`PROMO_PORT` 保持默认 `5174`（在后端 `CORS_ALLOW_ORIGINS` 白名单内，换端口会整页「无法连接后端」）。

### 子命令

| 子命令 | pnpm | 角色 |
|---|---|---|
| `full` | `promo:lv:full` | 导演台全流程：干净环境 + seek/变速/验收 |
| `repair` | `promo:lv:repair` | 定点补拍与坏帧修复；`--preset stills\|admit\|fixup\|patch\|rounds`（默认 `stills`）；`--only id,…` 限镜头 |
| `speed1-clip` | `promo:lv:speed1` | SPEED=1 流式短片 + `sequences/clip-streaming-debate-speed1` 抽帧 |

`repair` preset 对应能力：`stills`＝内容门禁坏帧修复；`admit`＝质询承认句 07/07b；`fixup`＝金句/承认句/终审 scroll 补拍；`patch`＝辩论室「第 N 轮」芯片中段静帧；`rounds`＝记分牌轮次覆写 04/05/05b/06/07。

环境变量常见：`PROMO_API` / `PROMO_TAPE` / `PROMO_OUT` / `PROMO_OVERWRITE`（勿默认 `PROMO_WIPE=1`）。细节与验收见 `assets/lv-molihua/MANIFEST.md`。

---

## 三、长片 · VIDEO_PLAN（规划中）

5–10 分钟产品功能说明视频的制作规划，见 [`VIDEO_PLAN.md`](./VIDEO_PLAN.md)。

**现状**：规划文稿；片头/片尾/章节方案有草案，**整片未实现**。与上两条线的关系：可复用 brand-30s Remotion 管线做片头片尾、可复用 lv-molihua（或同类）真机素材做中间屏录；成片仍走外部剪辑，不在本仓库闭环。

---

## 关联

- 磁带回放（演示基础设施）：[demos/README.md](/demos/README.md)
- 产品心智：[产品定位与品牌](/docs/01-产品/产品定位与品牌.md)
- 协作图 / UX 语义：[前端UX设计](/docs/04-前端/前端UX设计.md)、[编排器与CEO主Agent](/docs/03-AI核心/编排器与CEO主Agent.md)
