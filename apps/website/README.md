# GAgent 官网

协作智能平台 GAgent 的对外官网。

## 技术栈

- **Next.js（App Router）** + TypeScript，`output: "export"` 静态导出
- **Tailwind CSS v4**（CSS-first）
- 色系：**靛青**（深蓝近黑画布 + hue 200–275 单色相点缀）
- 构建产物为纯静态文件，部署 Cloudflare Pages

## 本地开发

```bash
cd apps/website
pnpm install
pnpm dev      # http://localhost:3000
```

本包在 pnpm workspace **之外**，请在目录内单独 `pnpm install`。

## 构建与部署

```bash
pnpm build           # 产物 → out/
pnpm deploy:pages    # Cloudflare Pages（需 .env.deploy.local）
```

## 设计依据

文案与定位取自 [`docs/01-产品/产品定位与品牌.md`](../../docs/01-产品/产品定位与品牌.md)。资产：Tool / Skill / Rule / Team。
