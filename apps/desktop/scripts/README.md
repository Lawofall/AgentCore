# apps/desktop/scripts

一眼区分：`package.json` / CI / electron-builder **点名的留下**；维护者一发 **不删、不进门禁**。

| 位置 | 用途 |
|------|------|
| **本目录（根）** | shoot、发版、sidecar、smoke、native rebuild；由 `package.json` 点名 |
| `video_capture/` | 真机捕获（`video:capture`） |
| `verify-cookies/` | Electron cookie 分区独立探针（不进门禁） |
| `__tests__/` | 脚本本身的单测 |

## 维护者一发（不进 package.json，勿删）

| 脚本 | 干什么 |
|------|--------|
| `repro-prepare-path.mjs` | 演示带 prepare-path 真跑复现 |
| `packaged-smoke.mjs` | 启动 `win-unpacked` exe，断言窗口挂上 |
| `smoke-demo-tape.mjs` | 演示带 auto-start 六拍点（`demos/README` 点名） |
| `raise-electron-window.ps1` | `shoot:graph-perf-live` 调起窗口 |

`release-channel.mjs` 由 CI / electron-builder 引用，不在 `package.json` scripts 里，同样常驻。
