# AgentCore 手机端（apps/mobile）

**Capacitor 8 原生壳**，产品页挂桌面 renderer 的 `dist-web`（对话 / 文件 / IM / 设置 / 登录同一棵树）。本目录留下推送 / 安全存储 / 语音 / Android 更新与 `android/` · `ios/`。**Android 侧载 APK 已落地**（见「Android 发版」）。壳内鉴权走 **Bearer + 安全存储**（WebView origin 不能靠同源 cookie）。

## 何时读这里

- 改窄屏产品页 / 对话 → [`apps/desktop`](../desktop/README.md) renderer（`dev:webapp`）
- 改 Capacitor 壳、Android 发版、CORS / FCM → 本目录
- 改 API / 执行语义 → [`apps/server`](../server/README.md)

## 文档入口

| 主题 | 文档 |
|------|------|
| 手机定位、减法、Capacitor | [`前端技术与架构` §五](../../docs/04-前端/前端技术与架构.md) |
| fold / 协议 | 桌面树；同文档 §十；根目录 `pnpm conformance`（只跑桌面） |
| 前端总读序 | [`前端地图`](../../docs/04-前端/前端地图.md) |
| 目录边界 | [`项目结构` §四](../../docs/02-架构/项目结构.md) |
| Android 发版 / CORS / FCM 闸 | [`发布与门禁` §7.6a Android APK（官网侧载）](../../docs/05-平台与运维/发布与门禁.md)；本目录命令见下 |
| clone 后跑通 | [`本地开发`](../../docs/02-架构/本地开发.md) §3 |

产品减法与商店 / iOS 余项以设计文档为准；远期能力见 [`产品路线图摘要`](../../docs/01-产品/产品路线图摘要.md)（提案全文不在公开仓）。

## 本地启动

产品 UI 用桌面 webapp（缩到 768px 以下即窄屏壳）：

```bash
pnpm -C apps/desktop dev:webapp
```

同步进 Android 工程：

```bash
pnpm -C apps/mobile cap:sync
pnpm -C apps/mobile android:open
```

离线看 AI 态走桌面 `#/preview`（`frontend-preview.mdc`）。

## 常用命令

| 命令 | 作用 |
|------|------|
| `pnpm -C apps/desktop dev:webapp` | 产品页（窄屏缩窗口） |
| `pnpm -C apps/mobile build` | 构建 desktop `dist-web` 供 Capacitor |
| `pnpm -C apps/mobile cap:sync` | 构建并 `cap sync` |
| `pnpm -C apps/mobile android:open` | 打开 Android 工程 |
| `pnpm -C apps/mobile android:sync-version` | 把 `package.json` version 写入 Gradle |
| `pnpm -C apps/mobile android:assemble` | 同步版本 + 签名 `assembleRelease` |
| `pnpm -C apps/mobile release:android` | 打正式 APK 并上传发布仓 draft |
| 仓库根 `pnpm gen:types` | 同步共享 REST / 事件类型 |
| 仓库根 `pnpm release:gate --only mobile` | 手机段（现为 fold-kit 测试；无 SPA 单测） |
| 仓库根 `pnpm conformance` | 只跑桌面 fold |

`deploy:pages` 已退役：独立 `m.example.com` 站已下线，不要再上传旧 SPA。浏览器窄屏走 `pnpm -C apps/desktop deploy:web`。

改 SSE / fold 后：仓库根 `pnpm conformance`（桌面）。

## Android 发版（侧载 APK）

产品机制、CORS、CDN、硬地板 → [`发布与门禁` §7.6a Android APK（官网侧载）](../../docs/05-平台与运维/发布与门禁.md)。本目录只留包内命令：

```bash
pnpm -C apps/mobile release:android
# draft 已存在时可：
pnpm -C apps/mobile release:android -- --skip-draft
```

签名：`android/keystore.properties`（gitignore；缺则脚本拒绝打 release）。需 **JDK 21** + Android SDK。FCM → 下文。

## FCM 推送（原生）

**无 `google-services.json` 时千万不要调用 `PushNotifications.register()`**——Android 会原生闪退（`FirebaseApp is not initialized`），JS `try/catch` 拦不住。`release:android` 仅在检测到 `android/app/google-services.json` 时注入 `VITE_PUSH_ENABLED=true`；否则推送整段 no-op，App 可正常用。

两份凭据必须出自**同一个 Firebase 项目**，否则设备 token 与发送方对不上：

| 凭据 | 从哪拿 | 放哪 |
|---|---|---|
| `google-services.json` | 控制台 → 添加 Android 应用，包名逐字填 `com.agentcore.mobile` | `apps/mobile/android/app/`（已 gitignore，勿提交） |
| 服务账号 JSON | 控制台 → 项目设置 → 服务账号 → 生成新的私钥 | 跑后端的机器上（勿入仓） |

顺带在 GCP 确认 **Firebase Cloud Messaging API (V1)** 已启用——代码打的是 v1 端点，legacy API 开着没用。

服务端（`apps/server/.env`；生产见 `deploy/config/production.env.example`「原生推送 FCM」段）：

- `PUSH_ENABLED=true`
- `FCM_SERVICE_ACCOUNT_PATH=/path/to/firebase-service-account.json`

改完**必须重启后端**——`build_push_sender()` 带 `lru_cache`，热改不生效。配错是静默降级（api 照常起），只在日志留 `push.fcm_unconfigured` / `push.fcm_init_failed`，所以开完先查这两条。

### 连本机后端验收（不必碰生产）

`resolveApiUrl()` 优先读 `VITE_API_URL`，所以真机包可以指向局域网里的开发后端；后端默认已监听 `0.0.0.0:8000`、CORS 默认放行 `capacitor://localhost`，两边都不用改：

```powershell
$env:VITE_API_URL="http://<局域网IP>:8000"; $env:VITE_PUSH_ENABLED="true"
pnpm -C apps/mobile cap:sync
pnpm -C apps/mobile android:assemble
```

`android:assemble` 只跑 gradle、不重新 build 前端，所以 `VITE_*` 必须在上一步 `cap:sync` 时给。手机与电脑须同一 WiFi。

真机验收三步：登录后 `GET /v1/devices` 能看到设备 → 触发「需要你」暂停 → 手机收到通知、点开深链到对应会话。

排查顺着日志走：`push.fcm_token_minted`（服务账号本身可用）→ `push.fcm_sent`（FCM 收下了，带 message_id 可去控制台追）→ 这两条都在却没收到，问题就在设备侧而非我们。`attention.signalled` 的 `push_outcome` 分四态，其中 `skipped_mobile_online` 是**有意不推**（手机 firehose 在线，走 in-app 横幅），别误判成故障。

## 贡献

[`CONTRIBUTING.md`](../../CONTRIBUTING.md)
