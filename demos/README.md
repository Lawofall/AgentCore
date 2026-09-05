# 产品演示 · 服务端磁带回放（dev-only）

把一次真实运行的事件流做成磁带，之后在**真实桌面前端**准备一条云端会话并按原节奏重放，便于人工录屏。不进产品功能面——靠环境变量开关；关闭后 API 404、命令面板无入口。

> **边界归属**：磁带回放与演示磁带属本目录（`demos/`）；宣传静帧 / 短片 / Remotion 成片属 [`apps/promo/`](/apps/promo/README.md)。

## 阶段 0 结论（本仓库打样素材）

| 项 | 值 |
|---|---|
| 磁带文件 | 本地自备（**不入公开仓**）；导出后放到 `demos/tapes/` 并由 gitignore / 私有拷贝管理 |
| 说明 | 早期 LV 商标辩题打样磁带与全场录像已从仓库移除，避免真实会话全量导出随仓库公开 |

- 磁带源 = 直播流录制导出（`demos/recordings/<message_id>.json` → `demo_tape_export.py`）。
- 宣传分镜见 `video-plan-lv-molihua.md` / `video-script-lv-molihua.md`（结论段留白，不对真实一审判决表态）。

## 桌面端主路径：准备模式（录屏推荐）

> 前提：本机已能正常跑产品（Docker / `uv sync` / `pnpm install` 已就绪）。命令与 [`docs/02-架构/本地开发.md`](../docs/02-架构/本地开发.md) 一致。

录屏时请**亲自在输入框打字发送开场消息**（更真实）。内容任意——绑定会话上发消息即触发磁带回合；会话里显示的用户消息就是你实际发送的文本。建议照磁带 `meta.user_prompt` 原话打字（命令面板准备好后会复制到剪贴板，可粘贴）。

### A. 启动后端（开回放开关）

在 `apps/server/.env` 增加或确认：

```env
DEMO_TAPE_REPLAY_ENABLED=true
# 可选全局默认（一键启动 / 绑定文件里可覆盖）
DEMO_TAPE_SPEED=4
DEMO_TAPE_MAX_GAP_MS=2000
```

然后：

```bash
cd apps/server
uv run python -m agentcore
```

改 `.env` 后必须**重启**后端。`demos/tapes/*.json` 与 `demos/bindings.json` 是热读的。

### B. 启动桌面端

另开终端：

```bash
cd apps/desktop
pnpm dev
```

用已有账号登录（开发种子账号：`dev` / `devpassword`，见 `seed_dev_user.py`）。

### C. 命令面板 · 准备会话

1. **Ctrl/Cmd+K** 打开命令面板。
2. 搜「演示回放」或磁带标题（如「茉莉」）→ 选 **「演示回放 · …」**（hint：开发 · 准备）。
3. 桌面新建**云端**空会话并绑定磁带，**不**自动开回合；建议开场词已复制到剪贴板。
4. 在输入框粘贴/照磁带原话打字，发送任意消息 → 磁带接管推流（多幕盘播**当前幕**；该幕结束后再发下一条消息推进下一幕）。
5. 看到 **开工卡 / team_preview** 时，在真实 UI 点「授权开赛」。
6. 辩论按磁带节奏推流；协作图在授权后出现。结束后 CEO 汇总落库。切走再切回侧栏详情应正常。多幕盘：末幕播完自动解绑；命令面板仍只有「准备 / 立即开播」两条（列表暴露幕数与首幕开场词）。

开关关闭或后端未开回放时，该命令**不会出现**（`GET /v1/demo-tape` → 404）。

不要：默认「快速对话」、本地项目会话——一键入口已强制云端裸聊，勿再改绑本地。

### D. 可选：HTTP 冒烟（不启桌面）

后端开着且 `DEMO_TAPE_REPLAY_ENABLED=true`：

```bash
cd apps/server
# 主路径：prepare → 发消息 → SSE → resume
uv run python scripts/demo_tape_http_walk.py --tape lv-molihua-trademark

# 备选：auto-start（等同 POST /start）
uv run python scripts/demo_tape_http_walk.py --tape lv-molihua-trademark --autostart
```

准备模式脚本会：`POST /v1/demo-tape/prepare` → `POST …/messages`（触发文本）→ SSE 收到 `team_preview_required` → `POST …/resume` continue → 继续收流到结束，并校验会话中用户消息 = 发送文本、节奏上限。

---

## 备选：立即开播（auto-start）

命令面板搜「立即开播」，或选 **「演示回放 · … · 立即开播」**（hint：开发 · 一键）。行为与旧一键相同：`POST /v1/demo-tape/start` 建会话、绑定、并以磁带原始用户消息直接开回合；接口会等到首个耐久暂停（`team_preview`）落库后再返回。

桌面 UX 验收脚本 `apps/desktop/scripts/smoke-demo-tape.mjs` 走这条 auto-start 路径（六拍点），不必手打开场。

---

## 备选：手动建会话 + bind 脚本

若需调试绑定文件本身，仍可用旧路径：

1. 命令面板 → **「云端随手聊」**（或工作区 chip →「云端草稿」）建云端会话。
2. 绑定：

```bash
cd apps/server
uv run python scripts/demo_tape_bind.py --latest \
  --tape demos/tapes/lv-molihua-trademark.json \
  --speed 4 \
  --max-gap-ms 2000
```

3. 在该会话再发一条任意消息（内容会被忽略，磁带接管）。

`--latest` 默认只挑云端会话。绑定本地会话默认**拒绝**；强绑需显式 `--include-local`（桌面通常回放不到；`DEMO_TAPE_REPLAY_ENABLED` 开启时 sidecar 也会对已绑定会话直接报错，不再静默降级成普通 AI）。

---

## 录制 + 导出磁带（已有打样时可跳过）

磁带源 = **直播流录制**（不再从 journal 反推）。两步：

1. **录制**：`apps/server/.env` 加 `DEMO_TAPE_RECORD_ENABLED=true` 并重启后端（sidecar 子进程读同一 `.env`，桌面需重启或重拉 sidecar 才生效）。之后每个真实回合的 SSE 流被原样录下（send / resume 各一段；真实节奏、含 EPHEMERAL 打字与心跳事件；可能含真实对话内容）——**云端**落 `demos/recordings/<assistant message_id>.json`（gitignored）；**sidecar 本地回合**落 `<userData>/sidecar/recordings/`（与 paused/outbox 邻居，打包用户不写仓库）。跑一次满意的真实回合即得素材。

   原片按 `message_id` 命名，列表/检索：

```bash
cd apps/server
uv run python scripts/demo_tape_recordings.py
uv run python scripts/demo_tape_recordings.py --query <conversation_id或关键词>
```

2. **导出**：

```bash
cd apps/server
uv run python scripts/demo_tape_export.py \
  --message-id <assistant message id> \
  --title "我的演示" \
  --out ../../demos/tapes/my-demo.json

# 多幕剧本：按播放顺序重复 --message-id（或 --recording）；每幕独立剪辑+门禁
uv run python scripts/demo_tape_export.py \
  --message-id <act1-id> \
  --message-id <act2-id> \
  --title "多幕演示" \
  --out ../../demos/tapes/my-multi.json
```

导出即剪辑 + 门禁：按 `TAPE_EXCLUDED_KINDS` 剪掉回合生命周期（message_start/end）、录到的暂停结算（冷路 `*_resolved` + 热路 `approval_resolved`，回放时现场重发）、回合元信息（turn_saved/标题/citations）与客户端工具请求（workspace/board/desktop notify——回放不得触发真实副作用），其余逐字节保留；随后跑入库脱敏双防线（剥 `run_context` system 内用户长期记忆 `<rules>` → 合成占位，保留块结构；再扫描记忆标记 / system 体内邮箱·手机，命中即拒绝——与 conformance `recording_cut` 共用 `demo_tape/sanitize.py`）。导出期另拒：未接线 pause（当前无——冷路 `team_preview` / `checkpoint` / `plan_review` 与热路 `approval_*` 均已接线；`--force` 可越过未接线类，**不能**越过客户端工具断言与脱敏扫描）。成品磁带断言不得含四类 `*_op_required` / `desktop_notify_required`（剪辑表之上的验证层）。原「下一步」followups chips **已产品下线**：导出若仍把历史 `followups_generated` 抬进 `meta.followups`，回放会忽略、不再落库/重发；开辩入口走阶段推进卡。`--user-prompt` 可覆盖 DB 查询（异机导出用）。磁带放仓库根 `demos/tapes/*.json`；命令面板按文件名 stem 列出。

## 导演控制台（第二屏 · OBS 录屏用）

回放开关打开时，后端另提供一个**不上镜的浅色控制室**页（OBS 第二屏遥控台），像播放器一样遥控正在注入的磁带（暂停 / 0.5–8× 倍速 / 章节跳 / 时间轴 seek）。控制通道长在服务端 player 的注入节拍器上，**产品前端一行不改**。回放模式下 uvicorn WatchFiles 关闭，控制室页会按 `director_page.py` 的 mtime 热读并自动刷新标签页，改 UI 无需重启后端。

### 启动

1. `.env` 开 `DEMO_TAPE_REPLAY_ENABLED=true`，照常起后端 + 桌面端，用命令面板准备/开播磁带。
2. 第二块屏浏览器打开：

```
http://localhost:8000/v1/demo-tape/director
```

3. 用开发账号登录（默认 `dev` / `devpassword`）→ 粘贴或从 sessions 下拉选中正在回放的 `conversation_id` → 即可遥控。

倒带（向后 seek）会**重开同一会话的回放回合**（清空该会话消息、从头爆发注入到目标点）。若桌面画面未立刻对齐，在侧栏点一下该会话触发恢复即可。

### REST（均需登录；开关关闭 → 404）

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/v1/demo-tape/director` | 控件页 HTML |
| `GET` | `/v1/demo-tape/director/sessions` | 当前进程内活跃回放 |
| `GET` | `/v1/demo-tape/director/{cid}/status` | 时刻 / 章节 / 倍速 / 状态 |
| `GET` | `/v1/demo-tape/director/{cid}/chapters` | 章节表 |
| `POST` | `/v1/demo-tape/director/{cid}/pause` | 暂停节拍 |
| `POST` | `/v1/demo-tape/director/{cid}/resume` | 继续（软暂停；不代点授权卡） |
| `POST` | `/v1/demo-tape/director/{cid}/speed` | body `{ "speed": 0.5..8 }`，瞬时生效 |
| `POST` | `/v1/demo-tape/director/{cid}/seek` | body `{ "t_ms" }` 或 `{ "event_index" }` 或 `{ "chapter_id" }` |

Seek 语义：目标点之前的事件去延时爆发注入；向后 seek = 重启式倒带。跨过 `team_preview` / `checkpoint` / `plan_review` / `approval_*` 等真交互点时自动代确认；落点在交互点上则停在该卡（不代点）。进度条 `t_ms` 吸附最近事件边界。

### 章节表生成规则

从磁带结构化事件预生成（非人工标注）：

| 章节 | 源事件 |
|---|---|
| 开场检索 | index 0 |
| 组队授权 | `team_preview_required` |
| 第 N 轮·立论 | `debate_round_started` |
| 第 N 轮·质询 | 该轮内首个 `run_id` 含 `_cx_` 的 `run_started` |
| 第 N 轮·打分 | `debate_round` |
| 终审 | `debate_result` |

## 倍速 / 间隔

| 参数 | 含义 |
|---|---|
| `speed` | `>1` 加快；原始间隔 ÷ speed |
| `max_gap_ms` | 单次等待上限（压住工具/思考长空窗） |

一键启动可用请求体覆盖；否则用 `.env` 全局默认。绑定文件优先于 `.env`（脚本路径）。导演台中途改倍速走 transport，不改 bindings 文件。

## 倍速实操备忘

- 原速 = `SPEED=1` + `MAX_GAP_MS` 抬到碰不着（如 `600000`）。本盘磁带回放总时长约 **22.3 分钟**，辩手深度思考时仍可能出现数十秒级静默——原速下的长静默是真实节奏，不是卡死。判断卡死的标准：发消息后 **3 秒内**连首批搜索活动都不出现。
- 宣传录屏建议：`SPEED=6` + `MAX_GAP_MS=2000`（辩论段墙钟约 3.5–4.5 分钟）；精剪审片可用 `SPEED=4`。录屏想压掉极端长等待：`MAX_GAP_MS=10000~15000`，其余节奏仍为真实。
- `DEMO_TAPE_RECORD_ENABLED` / `DEMO_TAPE_REPLAY_ENABLED` 开启时 `__main__.py` **自动关 WatchFiles**：原速 SSE 可达十几分钟，热重载的 `timeout_graceful_shutdown=2` 会硬杀 worker（桌面表现为「无法连接后端」、无 Traceback）。改代码后需手动重启后端。

## 设计决策（为什么长这样）

- **服务端磁带回放，而非前端注入**：重开会话/切页靠 REST 消息窗 + journal 水合，纯前端灌事件在用户切页时必穿帮；服务端回放落真实 DB 记录，一切页面行为天然成立。被否方案②：ScriptedProvider 重跑真实引擎——无 LLM 延迟导致节奏失真、prompt 漂移会对不上、工具副作用重复执行。
- **磁带源 = 直播流录制（EventSink dev tap），journal 反推层已退役（2026-07）**：`DEMO_TAPE_RECORD_ENABLED` 下 `demo_tape/recorder.py` 在 `runtime/events/sink.py` 的 emit tap 上把每个回合**实际发出的 SSE 流**原样录下——真实节奏 + journal 从不存的 EPHEMERAL 直播感（打字 delta、`tool_progress` 委派心跳、工具相位）天然在录制里；导出（`export.py: build_tape_from_recording`）按 `TAPE_EXCLUDED_KINDS` 剪辑，再跑脱敏/扫描/导出门禁（见上），**不做节奏或正文合成**。曾经的 journal 反推启发式层（时间窗铺满、worker 流式重建、委派心跳合成、正文/思考锚定切分，约 1100 行）连同其全部「已修勿回退」条目一并退役——录制流天然满足那些不变量。**事件字段与线上 SSE 契约对齐**（`type`/`timestamp` + pacing 超集 `t_ms`；格式版本 2）；存量 v1 磁带（`kind`/`ts`）读时别名兼容、**不做格式迁移**（内容治理脱敏可就地改 body）。被否方案：继续修 journal 反推——补丁史（同层 6+ 处已修勿回退、坏过两次的铺窗）证明该缝会持续出补丁。
- **回放身份 ≠ 录制身份（已修勿回退）**：磁带忠实保留录制时的 id，但桌面 InteractionStore 以 interaction id 为**跨会话全局键**（resolved 墓碑不复活、pending 首见保留），`pausedTurns.removeByCheckpoint` 也按裸 id 匹配——复用录制 checkpoint_id 时，同一桌面进程内**第二次回放**的 `team_preview_required` 被静默吞掉（历史事故：简介说完永久卡住、开工卡/协作图不出现、只等来「记忆已更新」卡）。修法：player 回放前按 `(本回合 message_id, 录制 id)` 确定性重铸**全部交互 id**（`demo_tape/identity.py`；send/resume 两段一致）。`run_id`/`execution_id`/`tool_call_id` 有意保持录制原值——各端按 message 域隔离、且字符串携带辩论结构（`debate_<exec>_r1_<side>`），重铸零收益反破坏投影。验收：`test_replaying_same_tape_twice_remints_distinct_checkpoints` + `test_real_tape_double_replay_mints_distinct_checkpoints`。
- **「下一步」followups 已下线**：产品不再 mint/展示 CEO→用户 chips；磁带回放忽略 `meta.followups`（存量磁带可留字段）。开辩仍走 `motion_card` → 阶段推进卡。
- **暂停即真实检查点**：磁带遇 `team_preview` / `checkpoint`（ask_user）/ `plan_review` 走冷路真暂停（落帧 + `POST …/resume`）；遇 `approval_*` 走热路真挂起（登记 `InteractionRegistry`、回合保持 running、`POST …/interactions/{id}` 热 resolve 后续播）——演示中人类拍板环节由录屏者掌控。挂起等待期间记忆 sweeper 会跳过该会话（`memory/consolidation.py` open-turn deferral，产品级修复）——不再出现「等授权时先弹记忆已更新卡」。冷路 `selected` / `adjust` 与热路 APPROVE/DENY/ALWAYS 均不改写后续磁带事件流（只记日志）；`stop` 走既有停止/salvage 路径并清理等待中的热路登记。
- **节奏坑（已修勿回退）**：磁带 `t_ms` 必须单调（`build_tape_from_recording` 对墙钟抖动做单调夹紧）；player 的 pacing 时钟不可回拨（曾在原速下表现为「正在思考」长卡死，4 倍速+2s 限幅时被掩盖）。
- **player 跳过不可发射事件再计步（已修勿回退）**：`turn_paused` 等非 SSE 事实必须在 pacing 计算**之前**跳过，否则它们推进节奏时钟——曾表现为点「授权开赛」后 11 秒静默（resume 首拍应即时发出）。
- **回放中断收口**：tape 分支与真实管线同样走 `CancelledError` salvage（`turn_runner.py`）——断流/停服不再留 `status=running` 僵尸行。
- **CEO 自持工具内联（已修勿回退）**：CEO 检索阶段的 `web_search`/`web_fetch` 在运行时带 captain 自己的 `run_id`；前端 `appendToolStep` 与后端 `_accumulate_process` 都把「带 run_id 的工具」当作 worker 工具从内联时间线剔除（本该落协作图节点），但检索阶段协作图尚未出现 → 前 ~15 秒只显示「正在思考」、检索活动全隐藏（正是上一条「3 秒内无首批搜索活动」判据的触发场景）。修法：回放（SINK 准备路径）对 `run_id == captain run` 的 `tool_use_*` 事件剥离 `run_id`，使 CEO 自持工具按渲染契约（conformance `single_agent` 向量：CEO 工具无 run_id）走 turn-level 内联。磁带数据保持忠实录制（含 run_id），仅在源适配层归一——现集中在 `agentcore/replay/legacy.py`（**legacy 例外，仅存量 v1 磁带需要**；退役条件：v1 磁带退役时一并删）。**真实产品同源已在 runtime 一并修掉**（旧磁带仍靠 player 层剥离兜底）：`execute_tools`（`runtime/engine/tool_exec.py`）对 `role=="captain"` 走 display/trace 拆分——`tool_use_*` 的 SSE 事件不发 `run_id`（内联渲染），`ToolCallFact`/熔断审计仍保留 captain `run_id`（§8.3 fold/溯源不变）；两处调用点 `tool_round.py`、`directive_apply.py`（coordination 收尾）均已传 `role`。
- **暂停缝正文与持久化对齐（已修勿回退）**：直播 SSE 在耐久暂停缝本就不发 `\n\n`——落库正文里的空行是持久化时 `join_segments(pre_pause, post)` 拼出来的。player 回放收口必须用同一 `join_segments`（曾用裸拼接 → 回放落库比 DB oracle 少 2 字节、严格字节保真红）；`demo_tape_fidelity_check.py` 亦按持久化规则重建正文再比 DB。热路 `approval_*` 不是耐久缝、无 joiner。
- **保真验收**：改录制/导出/回放层后跑 `apps/server/scripts/demo_tape_fidelity_check.py`（不要目测）——磁带 vs 原始会话 oracle 字节保真（正文 + `process_reasoning` 拼接思考）、结构/`t_ms` 单调不变量、以及**经真实 player 的离线回放校验**（暂停如期、回放身份已重铸、resume 后完成、live resolve 恰一次、回放正文/思考逐字节等于 oracle）。「tap 录制 → 出带 → 回放」闭环由 `test_recording_to_tape_to_replay_closed_loop` 常驻把守。

## 复用到新场景

开 `DEMO_TAPE_RECORD_ENABLED=true` 跑任何满意的真实回合 → 云端落 `demos/recordings/`、sidecar 本地落 `<userData>/sidecar/recordings/` → `demo_tape_recordings.py` 定位原片 → `demo_tape_export.py --message-id <id> --title … --out ../../demos/tapes/<新名字>.json`（sidecar 录制加 `--recording <绝对路径>`）→ 命令面板自动多出该磁带的准备/立即两条入口。也可：`uv run python scripts/log_timeline.py <conversation_id>`。

Promo 截图脚本（默认仍是茉莉花盘；新盘可直接换 tape）。导演台全流程已并入 `full` 子命令（勿再找已删除的 `*_director.mjs`）：

```bash
# 默认 = lv-molihua-trademark → apps/promo/assets/lv-molihua/
cd apps/desktop
pnpm promo:lv:full
# 等价：node scripts/promo_capture_lv_molihua.mjs full

# 新盘（输出默认 apps/promo/assets/<tape-id>/；可用 --out 覆盖）
node scripts/promo_capture_lv_molihua.mjs full --tape <新磁带stem>
node scripts/promo_capture_lv_molihua.mjs full --tape <新磁带stem> --out ../promo/assets/my-demo
```

也可用环境变量 `PROMO_TAPE` / `PROMO_OUT`。SHOT_MARKERS 仍偏茉莉花辩题文案——题材相近可复用，差异大时需改脚本内正则。

## 边界

- **不改** SSE / 协议契约、**不动**产品默认 UI（仅命令面板在开关开启时多准备/立即两条入口）。录制 tap 是纯观测缝（`sink.emit` 处理完后调用、异常只记警告不进回合），默认关闭。
- `demos/recordings/` 已 gitignore（原样录制、可能含真实对话内容）；入库素材只放剪辑后的 `demos/tapes/`。
- 回放 `cost_runs=[]`，尽量不写成本账本。
- 磁带交互点均已接线：冷路 `team_preview` / `checkpoint`（ask_user）/ `plan_review`（落帧 + resume）；热路 `approval_*`（InteractionRegistry + 热 resolve，回合不收口）。决策均按录制内容续播，不分支。
- 一盘磁带可含多幕（`turns[]`）：演示者逐条消息推进下一幕；`start` 只自动发第一幕；末幕播完自动解绑。存量单幕盘（顶层 `events`）读时归一为单幕，不改写文件。导出可按序传多个 `--message-id` / `--recording` 拼幕；单 id 用法与产物不变。导演台幕内 seek/章节照常，跨幕导航与 promo 多幕本期不做。
- 桌面误绑本地会话 → 发消息走 sidecar，服务端绑定无效。防护：`demo_tape_bind.py` 默认拒绑本地；回放开关开启时 sidecar 对已绑定会话返回显式错误（日志 `demo_tape.sidecar_local_session_bound`）；一键「演示回放」入口本身只建云端会话。
- `DEMO_TAPE_RECORD_ENABLED` / `DEMO_TAPE_REPLAY_ENABLED` 开启时启动日志会明示 **WatchFiles reload 已关闭**及原因；改代码后需手动重启后端。
- 入库素材须过脱敏扫描（用户记忆不进 `demos/tapes/`）；不建工具替身层——将来全链路回放若短路有副作用工具，落点在 `execute_tools`（按 `tool_call_id` 用录制 I/O），见执行引擎 §二边界。
