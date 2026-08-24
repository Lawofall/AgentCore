---
status: landed
code: apps/server/agentcore/messaging/
related:
  - docs/05-平台与运维/认证与会话.md
skip_if:
  - 只改 AI 对话（读 03-AI / 04-前端）
---

# 消息 IM（找人）

> **状态**：**P0（人 ↔ 人单聊）✅ + 内测全员群 MVP & 自助管理（退群/静音/置顶/成员面板）& 审核治理（平台 admin + 群管理员）& 富消息（图/文件）✅ 已落地**；**Admin「内测群」管理员任命 ✅**；**官方号产品公告广播 ✅**；**P1 在线态（部分）✅**；**回复引用 S1 ✅**；**@人/@所有人 S2 ✅**；**撤回 S3 ✅**；**编辑 S4 ✅**；**隐私设置面 ✅**；**好友关系 + 资料卡 / 通讯录 ✅**（见 §九）；**对方用户头像 ✅**（人侧 DTO 带与 `/me` 同公式的 `avatar_url`）；⏳ 官方号服务推送（任务/审批 deep-link）、P1 余项（已读 UI / 正在输入）、P2 余项（通用建群 + 群审核；人+AI 混合群见远期规划）、多 worker 实时。
>
> **定位**：**对话页 = 找 AI，消息页 = 找人**——复用前端聊天内核 + 实时通道，IM 另开后端表。

→ 见代码 `apps/server/agentcore/messaging/`、`api/routes/messages.py`、`api/routes/realtime.py`；前端 `renderer/services/messaging.ts`、`pages/MessagesPage.tsx`

---

## 一、定位与边界（✅ 已定）

| 决策 | 内容 |
|---|---|
| 双入口分工 | 对话页找 AI（保留），消息页找人（IM 收件箱）。纯 AI 团队群聊归对话页；消息页承载「人 ↔ 人」「官方号」「人 + AI 混合群（提案，详细不在公开仓）」 |
| 复用边界 | 复用的是**前端组件 + 实时通道**，不是同一张表 |
| 关系模型 | **双向好友图**（§九）：精确搜人仍开放（找得到 → 加好友），**默认仅好友可自由私信**；隐私档可放宽为 `anyone`。通讯录 = 已同意好友列表（不另做星标收藏） |
| 实时通道 | **每用户一条 SSE firehose + POST 发送**（§四） |

**被否决**：① 扩 `messages` 加 `sender_user_id` 复用同表——污染 AI 热路径表、跨域耦合 AI 与社交两套演进；改为新开 IM 表。② 起步用 WebSocket——要新传输 + 新鉴权、脱离现有 401 刷新纪律；先用 SSE firehose 复用基建，真成瓶颈再上 WS。③ **长期「非好友前置」**——群内无法从人头建关系、`who_can_dm=contacts` 无图可依；改为真·加好友（§九）。

## 二、数据模型（✅ 已落地）

遵循项目建模约定（UUID 主键、**无 ForeignKey**、`server_default`、按查询维度建索引；见 [`核心接口定义.md` §6.2](/docs/02-架构/核心接口定义.md)）。字段细节 → 见代码 `db/models/chat.py`、`db/models/users.py`（好友）。

| 表 | 说明 |
|---|---|
| `chats` | IM 会话；`auto_join=true` 标记「新用户默认入群」（内测全员群 + 全站唯一 `type=official` 官方号，见 §七） |
| `chat_members` | 参与者 + 每人会话态；`state=pending` 即陌生人「消息请求」门；`muted`=用户自静音、`muted_by_admin`=管理员禁言（可读不可发）；官方号默认 `pinned`、禁止 leave |
| `chat_messages` | 人向消息；`client_msg_id` 解断网重发去重；`system_card`+`payload` 承载产品公告（`kind=product_notice`）与二期服务 deep-link |
| `user_blocks` | 对称拉黑：断 DM + 双向搜索互隐；拉黑时解除好友（§九） |
| `user_directory_settings` | 隐私自决；缺行 = 可被搜到 + 默认可被加好友 + `who_can_dm=anyone`；`who_can_dm`=`anyone`/`friends`；`who_can_friend`=`anyone`/`group_members`/`nobody` |
| `friendships` | 双向已同意好友（规范序 `user_a_id < user_b_id` 唯一行） |
| `friend_requests` | 加好友申请：`pending` / `accepted` / `rejected` / `cancelled`；可带验证语 |

## 三、后端 API（✅ `/v1/messages`）

薄路由委托 `MessagingService`，权限在 service 层。

**关键决策**：**非会话成员一律 404**（IDOR 安全、不泄露存在性）；陌生人首条进 `pending` 消息请求门；发消息先按用户限流。

→ 见代码 `api/routes/messages.py`

## 四、实时通道（✅ 进程内；⏳ 多 worker）

- **传输**：`GET /v1/realtime` 每用户一条长连 SSE firehose（server→client），发送走上面的 POST。鉴权复用 Cookie；此流自带 401→刷新→重连（[认证与会话 §六](/docs/05-平台与运维/认证与会话.md)），前端客户端见 `renderer/services/realtime.ts`（§六）。
- **fan-out**：A 发 → 落库 `chat_messages` → 经 `HubChatEventPublisher`（`messaging/hub.py` 进程内 pub/sub）推送给在线成员的 firehose。
- **多载事件**：这条 firehose 不止 IM 消息——是该用户通用的「跨端 server→client」管线。除 `chat_message` 外，还载 `presence`（用户 `/v1/realtime` 连接数 0↔≥1 时推给**有共同会话**的对端；不入库）、`chat_changed`（会话/成员变更，见下）、`memory_updated`（记忆整合后由 `memory/consolidation.py` 广播，前端 `realtime.ts` 据此实时补对话内卡；走开时语义/配额 toast，情景摘要不 toast → [记忆 · §三 维护协议（情景沉淀 → 语义巩固）](/docs/03-AI核心/Agent记忆与知识系统.md)）、✅ `ai_attention`（**在等你**：热审批 / 拍板 / 冷卡；载 `state: required|resolved` + `conversation_id` / `turn_id` / `interaction_id` / `kind` / `title`；多端同权（B2）的 L1 层，手机据此弹横幅、桌面据此点亮「等你」灯，无在线端时转推送）。原 `workspace_promoted` 已随 auto-promote 链路移除（现为「文件夹即工作区」，见 [双模式工作区 §六](/docs/02-架构/双模式工作区.md)）。**扩展性**：新事件类型只需 `_format_event` 透传 + 前端 `handleFrame` 加一分支，无需新通道。**AI 侧的边界——只送信号不送内容**：`ai_attention` 只回答「哪条对话在等你」，卡体与正文仍走对话流 / REST；**禁止**把 AI 细流（`content_delta` 一类）挂上这条管线——它是账号级长连、每个在线端都收一份。账号级「哪些云对话还在跑」不走本 firehose、不扩 `ai_attention`、不转 FCM：由 `GET /v1/fulfill` 连接播种 `ai_turn_activity_snapshot`（`{ running }`，客户端 replace）+ 增量 `ai_turn_activity`。账号级「哪些对话在等你」的权威集合也走 fulfill：播种 `ai_attention_snapshot`（`{ entries }`，空表也推，客户端 replace）+ 增量 `ai_attention`（可与 realtime 并存；replace 只认 fulfill 序）。打开对话不清灯。
- **在线态（✅ 基本功能）**：在线 = ChatHub 上该用户 ≥1 条活着的 `/v1/realtime` 订阅（与 admin 同源）。REST 快照：`ChatParticipant.online`（会话列表 dm peer / 群成员面板）；实时：`presence` 事件。桌面呈现：单聊列表绿点 + 头「在线/离线」；群成员绿点 + 头「N 人在线」。不做：正在输入、last_seen、隐身、Redis TTL、手机端。
- **会话/成员变更（`chat_changed`）**：载荷 `{ type, chat_id, reason }`，`reason ∈ created | member_added | activated`，推给**受影响成员**——对方建 DM 推给 peer、加入群推给新成员、`pending → accepted` 激活推给双方。**薄事件**：不带 `ChatView`（unread / peer / 成员状态按查看者算，一个载荷服务不了两个成员），客户端收到自行重拉会话列表。**为何需要**：在此之前只有消息才驱动对端刷新，于是「对方建了会话但没发消息」「被拉进群」「消息请求被激活」都要等重启或断线重连才可见——被添加方看不到、添加方看得到。
- **离线补偿**：不另建表，上线时按 `last_read_message_id` 拉 `chat_messages` 增量。`chat_changed` 同样不入库，靠重连时的整表重拉兜底。
- **多 worker（⏳）**：`ChatEventPublisher` Protocol 已抽象（`events.py`），换 Redis / NATS 时为 seam 局部替换。现状 API 进程一律单 worker → [部署拓扑 · API 进程约束：多 worker 一律拒启](/docs/05-平台与运维/部署拓扑与环境.md#api-进程约束多-worker-一律拒启)。

## 五、隐私与反滥用（✅ 护栏；好友门见 §九）

精确搜人滥用面仍在；好友图落地后主路径是「搜到 → 加好友 → 私信」，陌生人私信仅当对方显式放开：

| 护栏 | 处理 |
|---|---|
| 防遍历 | 搜索按**精确**用户名 / ID，不做模糊枚举 |
| 隐私自决 | `discoverable`（可否被搜到）/ `who_can_friend`（anyone / group_members / nobody，默认 anyone）/ `who_can_dm`（anyone / friends，默认 anyone；原 `contacts` → `friends`） |
| 防骚扰 | **默认**：非好友不能开自由 DM（须先加好友）。对方 `who_can_dm=anyone` 时仍允许陌生人开 DM，peer 进 `pending` 消息请求（与好友申请分轨）。好友申请另受限流 |
| 拉黑 | `user_blocks` 对称，断 DM + 互隐搜索 + **解除好友 + 取消双方 pending 申请**；共享空间联动：挡新邀请 + 自动拒双方 pending（不自动移除已有成员，见 [双模式工作区 §八](/docs/02-架构/双模式工作区.md)） |
| 限流 | 发消息复用按用户限流（`conversation/rate_limit.py`）；好友申请按用户限流（防刷申请） |
| IDOR | → 见 [`认证与会话.md` §八](/docs/05-平台与运维/认证与会话.md) |

## 六、前端 MessagesPage（✅ 已落地）

桌面端「消息」两栏收件箱：复用对话页前端内核 + 实时通道，但走**独立 store / service**，与 AI 对话状态解耦。

**媒体显示路径（✅）**：桌面 cookie 鉴权 / 手机 Bearer `fetch` → `createObjectURL` → `<img>`；气泡用 `thumb_path ?? workspace_path`，lightbox 拉 `workspace_path` 原图；prod CSP `img-src` 显式含 `blob:`（只展示本页已鉴权字节，不放宽第三方远程图）。

→ 见代码 `apps/desktop/src/renderer/pages/MessagesPage.tsx`、`services/messaging.ts`、`stores/messaging.ts`

## 七、余项缺口（⏳）与内测全员群关键决策（✅）

| 项 | 现状 / 缺口 |
|---|---|
| 官方号(C) 推送 | **产品公告 ✅**：Admin `publish` 且 `surface∈{inbox,both,modal}` → 写入全站唯一 `type=official` 会话 1 条共享 `system_card`（`payload.kind=product_notice`），经现有 `chat_message` firehose 扇出；归档/过期不删 IM 历史、不回填。**双模板卡片 ✅**（`service` / `article` + 应用内详情）→ [管理员后台 · 官方号双模板](/docs/05-平台与运维/管理员后台.md#官方号双模板服务通知--图文)。任务完成 / 审批 → 官方号 deep-link **二期 ⏳** |
| P1 | 已读回执 UI、**在线态 ✅（见 §四）** / 正在输入 ⏳（typing 仍待）、**隐私设置面 ✅**、**好友 / 资料卡 / 通讯录 ✅（§九）**；**基础社交原语 ✅（§八）** |
| P2 | **人群聊：内测全员群 MVP + 自助管理 + 审核治理 + 富消息（图/文件）+ 群管理员 ✅**（`type=group` + `auto_join` + 退群/静音/置顶/成员面板 + 平台 admin **或** 群管理员踢人/禁言/公告 + Admin「内测群」任命管理员 + system_card + 图/文件附件）；通用建群 + 群审核仍 ⏳；**人 + AI 混合群**已迁提案——详细提案不在公开仓 / 维护者本地 |
| 多 worker 实时 | firehose / pub-sub 上 Redis / NATS（见 §四） |

> **内测全员群关键决策**（首个「人群聊」落地形态）：
>
> | 决策 | 结论与理由 |
> |---|---|
> | 默认进群机制 | `chats.auto_join=true` 标记「新用户默认入群」（迁移建群 + 回填活跃用户、`pinned=true`）；自动入群**只在注册时触发**、登录不重灌——否则退群永远失效（「可退群」语义前提）。被否：单建 `beta_group` 表 / 存 `beta_group_id` 配置（一行配置不值得建表；`auto_join` 列自描述、可扩展、查询直接） |
> | 治理权来源 | **双轨**：① 平台 admin（`users.role='admin'`，创始团队，超管）始终可治理；② 内测群**群管理员**（`chat_members.role='admin'`，仍无 owner）——仅在 **Admin 控制台「内测群」** 任命/撤销，IM 内不提供设管。群管理员可踢人/禁言/群公告/`@所有人`/群内治理撤回；不可进控制台、不可动平台 admin、不可互踢其他群管理员（仅平台 admin 可管群管理员）。Roster：`is_admin`=平台角色；`group_role`=群角色；徽章分标「平台管理员」（短「平台」）/「群主」/「管理员」（owner≠admin；平台优先于群角色）。挂在群聊对方气泡昵称旁 + 成员列表；@ 菜单副标题。图形：冠 / 盾 / 齿轮（不复用官方号 BadgeCheck、不加金色）。被否（旧）：内测群完全不用群级 role、只靠升平台 admin 当管理员（过权）。仍否：内测群指定**群主**/转让/解散（全员 `auto_join` 无自然 owner） |
> | 禁言存储 | 新列 `chat_members.muted_by_admin`（不复用 `state`，避免污染 accepted/pending 消息请求门）；禁言=可读不可发（send 403），管理员豁免。被否：`state='muted'`（语义混淆） |
> | 系统提示范围 | 只发**公告 + 踢人**（`system_card`，NULL sender=official 居中胶囊）；入群/退群/禁言**不发**全群提示（全员群每次注册自动入群会刷屏；禁言改发言时 403 toast）。禁言端点 `POST .../mute`（toggle） |
> | 群内隐私 | roster 暴露成员显示名（内测社区可接受）；`discoverable=false` 隐身**不掩盖**已在群内身份；群内被拉黑者消息 MVP 仍可见（客户端过滤为后续可选项） |
> | 内测后归宿 | 转放量时该群保留 / 拆主题多群 / 关停 → 路线图「远期放量」；详细提案不在公开仓 / 维护者本地 |

## 八、基础社交原语（✅ 已落地）

> **目标**：补齐「找人」会话的线程感与可治理性——回复引用、@人/@所有人、撤回与编辑；对齐主流 IM 习惯，优先做透每一档，不与远期 `@agent` 混合群绑死。  
> **现状锚点**：**S1 回复 ✅**；**S2 @ ✅**；**S3 撤回 ✅**——`POST .../messages/{id}/recall`；2 分钟窗；平台 admin **或群管理员**群内治理撤；`system_card`/官方号仅平台 admin；`recalled_at` 软撤 + `chat_message_updated` 原地替换。**S4 编辑 ✅**——`PATCH .../messages/{id}`；15 分钟窗；仅本人纯文本；`edited_at`；附件/已撤/官方/system_card 拒；同帧 `chat_message_updated`。  
> **范围外（本轮不做）**：emoji 反应、转发到他聊、消息搜索全文、置顶单条消息、正在输入/已读 UI（仍归 §七 P1）、`@agent` 接编排（远期）。

### 8.1 关键取舍（已定）

| 决策 | 结论 | 理由 / 行业对齐 |
|---|---|---|
| 回复引用 | 发消息可带 `reply_to_message_id`；服务端校验**同会话且存在**；响应与 firehose 带**轻量引用快照**（发送者显示名 + 正文截断或附件类型标签）；原消息撤回/删除后引用条仍显示快照，文案可标「原消息已撤回」 | 微信/飞书：引用靠快照，避免原消息一撤全链断裂；只回传 id 会逼客户端二次拉取 |
| @人 | 结构化 `mentions[]`（`user_id`，可选 offset）；**禁止**只靠正文正则当真源；被 @ 者必须是本会话成员；composer `@` 弹出本群成员；气泡内高亮 | Slack/Discord：mention 是一等数据，便于未读与通知策略 |
| @所有人 | **做**；群内仅 **平台 admin 或群管理员**（`group_role∈{owner,admin}`）可发；普通成员只能 @具体人。通用自建群落地后可再开「群主可配」 | 全员群无限制 `@所有人` = 骚扰面；对齐 Slack `@channel` 权限门 |
| 静音 × 被 @ | 用户自静音（`muted`）时：被 @（含合法的 @所有人）→ **会话列表角标加强 + 桌面弱通知**（可点进会话）；不弹强模态。管理员禁言（`muted_by_admin`）仍不可发，与通知无关 | 微信：静音仍可被 @ 提醒；全员 @ 用弱通知降打扰 |
| 撤回 | 本人发送后 **2 分钟内**可撤回；平台 admin **或群管理员**可撤群内任意成员消息（治理，不受 2 分钟限）。撤回后气泡变为「xxx 撤回了一条消息」占位，**保留行**（不物理删），引用快照仍可读。`system_card` / 官方号公告：**用户不可撤**；仅**平台 admin** 可撤治理 | 微信 2 分钟；保留行避免已读游标与引用悬空 |
| 编辑 | 本人文本消息可编辑（**15 分钟内**）；标 `edited_at`；附件消息首期不支持改附件（只能撤了重发）。已撤回不可再编辑 | 飞书/Slack「已编辑」标记；附件改写成本高，首期砍掉 |
| 与对话页 `@` | IM `@` = **人**（及日后 Agent 分区）；对话页 `@` = 附件/路径（file/dir/conversation）+ 可选 **`agent_mentions` 软点名**（非强制派单）——**两套语义、两套 UI，禁止混用组件当真源**。对话页点名 ✅ 落 `messages.agent_mentions` JSONB，经 `MessageDetail.agent_mentions` 读回历史用户气泡角色芯片；**禁止**塞进 `MessageAttachment.kind` | 术语已分域 |
| 客户端节奏 | 契约与桌面先做透；手机跟渲染与入口，不阻塞桌面验收 | 单契约多端 |

### 8.2 分阶段与验收（慢慢做透）

| 阶段 | 内容 | 验收要点 |
|---|---|---|
| **S1 回复可用化** ✅ | 校验 + 引用快照 API/事件；桌面：回复入口、composer 引用条、气泡引用块、点击滚到原消息 | 跨端收到带快照的回复；非法 `reply_to`（跨会话/不存在）→ **422**；乐观发送与 firehose 一致；快照落库列 `chat_messages.reply_to`（JSONB，冻结 `sender_user_id` / `sender_display_name` / `body_preview`，预览截断 100 字、空白折成单行） |
| **S2 @人 + @所有人** ✅ | `mentions` 落库与校验；composer `@` 菜单；高亮；静音弱通知；平台 admin **或群管理员** `@所有人`（群） | 非 accepted 成员 id → 422；普通成员发 `@所有人` → 403；单聊 everyone → 422；静音用户被 @ 有列表角标 + 弱通知；未读策略不破坏现有 `last_read_*` |
| **S3 撤回** ✅ | recall API + firehose `chat_message_updated`；2 分钟窗；平台 admin / 群管理员治理撤；官方/system_card 仅平台 admin | 超时本人撤 → 403；撤后引用仍显示快照；列表预览不露出已撤正文 |
| **S4 编辑** ✅ | `PATCH .../messages/{id}` + `edited_at`；气泡「已编辑」；15 分钟窗；composer 编辑态 | 附件消息拒编辑；已撤拒编辑；他端实时看到正文替换 |
| **（并行可选）** | §七 P1：正在输入（单聊优先）、已读 UI（单聊） | 不阻塞 S1–S4；typing 不入库 |

### 8.3 契约方向（实现时细化，此处锁语义）

- **发送**：延续 `POST .../messages`，扩展可选 `reply_to_message_id`、`mentions`；`@所有人` 用约定 sentinel（如 `mentions` 含特殊 `user_id` / `kind=everyone`——实现时二选一写进 OpenAPI，禁止魔法字符串散落前端）。
- **变更**：撤回走独立写接口 `POST .../messages/{message_id}/recall`，经 firehose 扇出显式 `chat_message_updated`（勿复用 `chat_message` 以免误加未读）；客户端按 `message_id` 原地替换。编辑（S4）同模式。
- **快照**：引用预览字段与消息同生命周期返回；不另建「引用表」。
- **被否**：① 用正文 `@Name` 正则当唯一真相源；② 撤回物理删除行；③ 全员群开放全员 `@所有人`；④ 首期做反应/转发冒充「基础能力」。

## 九、好友关系与资料卡（✅ 已落地）

> **目标**：群内点头像 → 资料卡 → 加好友 / 发消息 / 拉黑；完整申请流 + 通讯录；默认仅好友自由私信。  
> **改写**：废止「非好友前置」；「联系人收藏」由**通讯录（已同意好友）**替代，不另做星标。  
> **客户端**：契约 + 桌面 ✅；手机跟渲染，不阻塞桌面验收。  
> **范围外**：已读/正在输入、通用建群、手机跟版。

### 9.1 关键取舍（已定）

| 决策 | 结论 | 理由 |
|---|---|---|
| 关系图 | 双向好友；申请 → 同意后写入 `friendships`；拒绝/取消不建友谊 | 对齐微信；单方「收藏」无法支撑「仅好友可私信」 |
| 私信门 | **默认仅好友**可 `POST .../chats/dm` 且双方 accepted；对方 `who_can_dm=anyone` 时陌生人仍可开 DM（peer `pending`） | 完整加好友；保留宽松档给愿收陌生人私信者 |
| 消息请求 vs 好友申请 | **分轨**：好友申请是主路径；`pending` DM 仅服务 `who_can_dm=anyone` 宽松档 | 合并两套状态机易糊；主路径不引导陌生人先发消息 |
| 谁可加我 | `who_can_friend`：`anyone`（默认）/ `group_members`（须有共同 `type=group` 会话）/ `nobody` | 默认开放与现搜人一致；可收紧 |
| 同群可见 | `discoverable=false` **不掩盖**已在群内身份（与 §七 群内隐私一致）；资料卡仍可从群打开 | 群 roster 已暴露显示名 |
| 同意好友 | 接受申请后：建 `friendships`；DM 不存在则建（双方 `accepted`）、已存在则激活；发一条 `system_card`（`kind=friend_accepted`）「我通过了你的朋友验证请求…」 | 对齐微信：同意后消息列表两边都立刻出现会话，不必再找入口开聊 |
| 删除好友 | 删 `friendships`；**不**自动删 DM 历史；再私信按当前 `who_can_dm` 门 | 历史会话保留 |
| 拉黑 | 对称拉黑 + 解除好友 + 取消双方 pending 申请 + 既有断 DM/搜隐 | 一处收口 |
| 实时 | firehose 增 `friend_request`（新申请/对方处理结果）；同意后另发 `chat_changed`（§四）让两端会话列表即时出现该会话；不入库补偿——上线拉申请列表 | 与 presence 同通道 |
| 搜人入口文案 | 「发起会话」可保留搜人，结果进资料卡（加好友/发消息按关系），禁止暗示「无好友即可自由私信」为唯一路径 | 产品诚实 |

### 9.2 数据（语义）

- **`friend_requests`**：`id`，`from_user_id`，`to_user_id`，`message`（验证语，可空，限长），`status`（`pending`/`accepted`/`rejected`/`cancelled`），时间戳。同一对用户至多一条 `pending`（任一方向）；已是好友则拒新申请。
- **`friendships`**：`user_a_id < user_b_id` 唯一；`created_at`。无「备注名」首期。
- **`user_directory_settings`**：加 `who_can_friend`；`who_can_dm` check 改为 `anyone`/`friends`；迁移：`contacts` → `friends`。

### 9.3 API 契约（锁语义；路径挂 `/v1/messages`）

| 方法 | 路径 | 语义 |
|---|---|---|
| `GET` | `/users/{user_id}/profile` | 资料卡：显示名/用户名/头像（`avatar_url`，与 `/me` 同公式，可空）/在线/关系（`self`/`none`/`outgoing_request`/`incoming_request`/`friends`/`blocked`）+ 相关 request id；非可见目标 → 404（不泄露） |
| `GET` | `/friends` | 通讯录（已同意好友列表） |
| `GET` | `/friends/requests` | 申请箱：`incoming` + `outgoing`（仅 `pending` 为主；实现可附带近期已处理） |
| `POST` | `/friends/requests` | 发起申请（`user_id` + 可选 `message`）；校验 `who_can_friend`、拉黑、非自加、非已好友；限流 |
| `POST` | `/friends/requests/{id}/accept` | 仅 `to_user`；建友谊；清该对 pending；建或激活 DM 并发 `friend_accepted` 系统卡 |
| `POST` | `/friends/requests/{id}/reject` | 仅 `to_user` |
| `DELETE` | `/friends/requests/{id}` | 仅 `from_user` 取消 pending |
| `DELETE` | `/friends/{user_id}` | 删除好友 |
| `POST` | `/chats/dm` | **改门**：已是好友 → 正常开/回 DM；非好友且对方 `who_can_dm=friends` → **403**；非好友且对方 `anyone` → 沿用 pending 消息请求 |
| `GET`/`PATCH` | `/directory` | 扩展 `who_can_friend`；`who_can_dm` 枚举 `anyone`\|`friends`（旧 `contacts` 读兼容已退役） |
| 既有 | `/blocks*` | 拉黑时级联解好友 + 取消 pending 申请 |

Firehose：`{ type: "friend_request", action: "created"|"accepted"|"rejected"|"cancelled", request: {...} }` 推给相关方；同意时另发 `chat_changed`（§四）给会话双方。

**人侧头像（✅）**：凡画出「这个人」的 IM 表面（列表单聊对方、单聊头栏、气泡、资料卡、通讯录、申请箱、搜人、黑名单、群成员、@ 菜单）有则出图、无则字头。人侧 DTO（`PersonPublic`：`ChatParticipant` / `UserProfile` / `FriendSummary` / `UserSearchResult` / `BlockedUser`）带与 [`UserResponse.avatar_url`](/docs/05-平台与运维/认证与会话.md) 同公式的相对路径（`/v1/users/{id}/avatar?v=<hash>`；无 `avatar_key` 则为 `null`，客户端不猜路径、不请求取图）。**消息体与薄事件不带头像**（`ChatMessageDetail` / `ReplyToSnapshot` / `presence` / `chat_changed`）；群气泡用 `sender_user_id` 查 roster，单聊用 `peer.avatar_url`。`ChatSummary.avatar_url` 仍是**会话图标**预留（群徽 / 官方号），禁止写入用户照片。换头不推 firehose：对端下次拉列表 / 成员 / 资料卡 / 通讯录拿到新 `?v=` 再换图。

### 9.4 桌面 UX（验收）

| 表面 | 行为 |
|---|---|
| 群气泡头像 / 群成员行 / 单聊头栏 | 有用户头像则出图、无则字头；可点 → 资料卡 |
| 资料卡 | 按关系：加好友（可填验证语）/ 已申请 / 同意·拒绝 / 发消息 / 删除好友 / 拉黑 |
| 通讯录 | 消息页入口；列表 → 资料卡或进 DM |
| 新的朋友 | 申请收件箱（角标）；同意/拒绝 |
| 消息隐私 | 可被搜索 + 谁可加我 + 谁可私信我；已拉黑列表入口 |
| 搜人 | 结果点开资料卡，不再「一点即开自由 DM」冒充唯一路径（好友可直达发消息） |

### 9.5 验收要点

- 群点头像 → 资料卡 → 加好友 → 对方同意 → 双方通讯录有对方 → 「发消息」进同一 DM  
- 非好友 + 对方 `who_can_dm=friends` → 开 DM **403**；对方 `anyone` → 仍可消息请求  
- `who_can_friend=group_members` 时：无共同群申请 → 403；同群可申请  
- 拉黑后：友谊解除、申请取消、不可再申请/私信、搜索互隐  
- `discoverable=false`：搜不到，但群内资料卡仍可开  
- 单测覆盖门控；桌面可点验上述路径  

**被否**：① 仅做前端假「加好友」无服务端图；② 好友与消息请求合并成同一状态机冒充简化；③ 首期做好友备注/分组/朋友圈。
