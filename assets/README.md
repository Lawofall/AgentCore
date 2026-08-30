# 根级品牌素材

仓库根 `assets/` 只放**跨应用、与运行时无关**的品牌资源（图标、Logo 源文件等）。

| 放这里 | 不放这里 |
|--------|----------|
| 安装包 / 官网 / 文档共用的图标、设计稿导出的 PNG/SVG 母版 | 各 app 运行时静态资源 → 对应 `apps/*/public/` |

应用内引用品牌图时，从本目录复制或经构建脚本同步到目标 `public/`，**不要在各 app 各存一份未同步的副本**。

Orbit 桌面图标母版：`agentcore-icon-orbit-cropped.png`（满铺、四角不透明 → macOS 打包 `apps/desktop/build/icon-mac.png`）；`agentcore-icon-orbit-rounded.png`（四角透明 squircle → Windows/Linux 打包 `icon-win.png` 与运行时 `apps/desktop/resources/icon.png`）。改母版后二进制复制到上述路径，勿重生成失真图。
