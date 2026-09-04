---
status: landed
code: apps/server/agentcore/runtime/events/,apps/server/agentcore/replay/
related:
  - docs/03-AI核心/执行引擎架构设计.md
  - docs/04-前端/前端技术与架构.md
  - docs/05-平台与运维/部署拓扑与环境.md
  - demos/README.md
skip_if:
  - 只改 DAG/ReAct（读执行引擎）
---

# SSE 事件与录制回放

> **权威**：SSE 事件协议、契约生成、多端跟播、录制/回放。前端消费 → [前端技术与架构 §十 SSE 与协议一致性](/docs/04-前端/前端技术与架构.md)。
>
> **主循环归属**：「**看**」的传输底座——协作过程能被看见，全靠这条事件流 → [主循环](/docs/01-产品/产品定位与品牌.md)。

## 一、事件协议

清单 → 见代码: `runtime/events/types.py`（`EventType`）+ `packages/contract-types`。  
处置权威 → `runtime/events/disposition.py`：DURABLE 入 journal；DERIVED 走专用列；EPHEMERAL 有意不落库。
字段消费棘轮（叶名零命中，存量按语义分组豁免，只拦新增未读叶）→ `agentcore/conformance/field_consumer_baseline.py`。

**接缝决策**：
- **`run_escalation`**：worker 调 `escalate` 瞬间即可见（DURABLE + `escalation_id`）；工具经 `on_escalate` 回调，不碰事件词表。escalate 仍非阻塞。
- **幕序列 `act`**：协作图 = 幕序列；旧 journal 无 `act` → fold 合成单幕。编排 → [辩论编排](/docs/03-AI核心/辩论编排设计.md)；渲染 → [协作图 UX](/docs/04-前端/协作图与双视图UX.md)。
- **`run_phase`**（✅）：worker mid-flight 活动相位（`thinking` / `tool` / `waiting_children` / `winding_down`）——EPHEMERAL；投影 `run.phase` / `phaseTool`。`queued`=`status:pending`，`skipped`=`status:skipped`。→ 见代码：`runtime/events/run.py:run_phase`
- **`turn_queued`**（✅ EPHEMERAL）：同对话 FIFO 排队确认（`queue_id` / `position` / `queue_depth`；经典+steer 回落时带 `degraded_from: "steer"`）。协调插话升 FIFO 时亦向 live sink 发射（可进条、可取消）。三条入队路（经典 FIFO / 协调升队 / steer 收口回落）均经**对话信号道**广播给跟播该会话的各端，一条连接只收一份；内容权威仍是 `GET …/queued-turns`，本帧只作「变了」信号。
- **`turn_queue_started`**（✅ EPHEMERAL）：FIFO 出队开跑——**时间线用户泡入场帧**（正文在帧上：`content` 必填；可选 `attachments` / `agent_mentions`，空则省略；另有 `queue_id` / `conversation_id` / `remaining_depth`）。`pop_next` 后、`stream_chat` 前作为**新回合 sink 首帧**（先于 `message_start`）。客户端据此清该 `queue_id` 轻态并插用户泡——**否决**靠 `message_start` 猜出队；**否决**出队先落库用户行。reload 靠 REST。`turn_queued` / `turn_queue_cancelled` 仍只是「变了」信号。
- **`turn_queue_cancelled`**（✅ EPHEMERAL）：按项取消成功（`queue_id` / `conversation_id`）；多端清 UI——经**对话信号道**送达跟播各端，**无 live run 时亦然**（队列可比宿主回合活得久，撤单发生在空档期时对端此前什么都收不到）。语义 → [运行时三模型 · 同对话再发](/docs/03-AI核心/运行时三模型与挂起.md#同对话再发steer--queue)。
- **`resume_settled`**（✅ EPHEMERAL）：冷卡「继续」提交时帧已被上一次续跑吃掉——回 200 + 本帧（`kind` / `checkpoint_id` / `decision` / `decided_at` / `turn_status`）而非 404，客户端据此把卡收成结果态；`turn_status=running` 表示同连接紧接着续那次跑的流。本帧只是这条连接的幂等 ack（与 `resume_deferred` 同款）：执行事实的权威是 `turn_journal`，**结算结论**的权威是 `paused_turn_outcomes`（抢到帧的那一方同事务写下），本帧转述的是赢家那份，不是本次提交的那份。语义 → [运行时三模型 · 冷 resume 与 live 交叉](/docs/03-AI核心/运行时三模型与挂起.md#冷-resume-与-live-交叉deferred)。
- **`user_interjection`**（✅ DURABLE）：运行中插话（经典 steer + 协调共用）；同 `interjection_id` 保最新 `status`。协调：`received` → `injected` → `addressed` / `queued` / `failed`；经典：`received` → `injected`（终态）/ `queued` / `failed`（无 `addressed`）。`injected` = 内容真正进模型上下文。POST 短流 ack 看 `received`。可选 `attachments`（名字 + 路径 + 二进制标记）与可选 `agent_mentions`（`{agent_id, role}` 软芯片，非硬路由；旧客户端忽略）。→ 见代码：`runtime/events/run.py:user_interjection` · `runtime/turn/steer.py` · `runtime/coordination/interjections.py`
- **`workspace_lock_wait`**（✅ EPHEMERAL）：同 folder 写锁短等（A′ 后仅写路径争用）；`waiting` 进出。桌面空气泡「等待工作区…」——**不得静默等锁** / 禁空 Thinking… 冒充。与同对话 `turn_queued` 正交。→ 见代码：`workspace/locks.py` · `runtime/events/run.py:workspace_lock_wait`
- **`replace`**（✅ additive；四条正文类 delta：`content_delta` / `reasoning_delta` / `run_output_delta` / `run_reasoning_delta`）：帧级标记，`true` = 本帧携带该通道**末尾那个尚未闭合的文本块**的完整内容，客户端换掉那一块而非追加（末尾已被工具/标记步闭合时当普通新块折）。attach 回放段专用，覆盖两种全文帧——按通道合成的整块，以及增量段里跨游标那步 `process_*` 行携带的整步全文（客户端手里只有它的前半截）；两者互斥（已被 process 覆盖的通道不再出合成块），故一个语义够用。live 帧永不带。→ 见代码：`runtime/events/attach_replay.py`
- **`run_failed.failure_kind`**（✅ additive）：协作图失败脸优先按此类贴文案——`quality`→「未达标」、`format`→「格式未过」（结构/格式闸：缺章节·JSON）、`model`→「模型中断」、`call`→「调用失败」；缺省→「失败」/空 error「调用失败」。禁前端扫正文猜脸。→ 见代码：`RunFailureKind` · `runtime/events/payloads/run.py`
- **`tool_use_end.status=redirect`（✅ additive）**：选错工具通道（短执行里 dump/grep 源码、`read_url`↔`file_read` 互斥、长驻走错短核等）运行时拒执行并改道。项目级 install/test/typecheck **不是**改道——统一 `run` 按命令分类进验证核直接跑。模型面 `result` 仍是失败回执；过程步不是 `error`。旧 journal 的 `status=error` + 改道 `failure.code`（含已停发的 `project_verify_redirect`）由 fold 归一为 `redirect`。→ 见代码：`runtime/engine/tool_channel_redirect.py`
- **`message_end.team_batch`**（✅）：本回合团队状态，turn journal 派生。`no_batch` | `in_flight` | `settled`。没派工是确定态。不进 ProjectedTurn（旁路字段，与 `collab` 同）；**用户面不渲染**。→ [执行引擎 · 团队状态](/docs/03-AI核心/执行引擎架构设计.md)

`finish_reason` → 见代码 `FinishReason`。

## 二、契约生成

后端 dataclass = SSE 类型唯一真相源；`pnpm gen:types` 反射生成 TS；CI `contracts` job 漂移门禁。改事件后必跑 `pnpm gen:types` **与** `pnpm conformance`。

## 三、多端跟播

**跟播** = 同一回合的事件流被多个客户端同时或先后订阅观看。桌面与手机共用同一端点 `GET …/stream?follow=true`（对话级长订阅：空闲只收心跳，此后每个新回合自动重放 + 跟播）。

- **扇出**（✅）：每订阅者一条独立有界队列，同帧逐个投递——不瓜分、一端断开不连坐、`seq` 在 emit 侧一次性回填故各端编号一致。→ 见代码 `runtime/events/sink.py`
- **接入姿势**（✅）：重放段 → `: attach-caught-up` 边界 → 实时段。客户端据此把首段整段缓冲后一次折，否则已完成的 worker 会再演一遍 running→completed。一次折约束的是**协作图**；打开/刷新时消息窗正文先揭开，不等这条回放。**边界注释是唯一折段闸**：注释前断流 = 传输失败（跟播丢缓冲后重连，回合级 attach 抛 network 由调用方重试）；游标只在折/dispatch 后推进，半段不入屏。**否决**流结束仍无注释就把缓冲当完整段折（旧后端兼容）。**执行端同会话**：本端 POST / sidecar 泵占用时对话级 follow **静音不断连**（帧不折、游标不推进），放闸后同一条 SSE 接着收；禁止 abort 再全量 `full_replay` 把本机刚折完的回合闪一次。
- **增量重放**（✅）：`Last-Event-ID` 决定**发什么、不决定读什么**——服务端照旧读整回合 journal，因为四处判定（是否结构化回合 / 已覆盖的 run 集 / `agent_id` 回填 / worker 全文拼接）必须扫过全表才成立，改成 `seq > 游标` 查会让它们翻面成「worker 正文整段重发或静默丢失」。贵的是网络与客户端折，不是那一次主键索引扫描，故**过滤发生在产出事件之后**。attach 下发的 `tool_use_end.result` 与过程车道同为 8k 预览（live 主连接仍全文；完整 stdout 在 `tool_call`）。→ 见代码 `runtime/events/attach_replay.py`

**两条指令都由服务端下达，客户端不拿屏上状态猜**——没有它们的年代，桌面 follow 重连分支猜错把正文折了两遍：

- **`full_replay`**（段级，落在段首 `message_start`）：本段是整回合重放，**原位**清空该 `message_id` 的正文 / 思考 / process / 执行槽再折本段（保留气泡 id，避免换泡把已画 Markdown 卸掉）。增量段与 live 首帧都不带 = 同回合重开、往后接。两条重放路径同令：走 journal 的合成段首，走 sink 内存历史的把段首那帧改写成带标记的副本（有内容却没有 `message_start` 就补一帧）。
- **`replace`**（帧级，落在四条正文 delta）：本帧带的是该通道末尾那个**尚未闭合**的文本块的全文，换块而非追加。重放段里有两类帧天生是全文——通道快照、以及跨游标的那一步（process 行在步骤闭合时才落盘，而客户端手里只有它的前半截）；按追加折就会叠出「根据搜索，根据搜索，答案如下」。live 帧永不带。

**拿不准就退回全量**：增量是纯优化，全量那条路已被证明正确。故**否决**世代校验与游标对账——游标不可信的形态（收口/挂起落盘走 `record()` 会整回合重编号、按会话存故跨回合漂来的外来值、压根不上线的执行车道事实）一律整段重发，不去修补。**空段则连指令都不下**：reset 后面什么都不跟，等于把客户端的正文擦了不还，而「sink 历史为空」≠「客户端手里为空」——冷卡二次「继续」挂上活着的续跑时，续跑 sink 刚建、历史空着，客户端却握着暂停前的整轮正文，且这条路只走 sink 历史、不查 journal。

**五条边界都是有意取舍，不是待修 bug**：

- **增量段里结构配对天然不完整**：`tool_use_end` 的 start 在游标前、run 节点缺 `run_plan` 图锚。这是对的——客户端有前文，不补发、不自愈。
- **扇出是进程内的**：`turn_runs` / `conversation_hub` 均为进程内单例，故多 worker 启动即拒 → [部署拓扑 §六](/docs/05-平台与运维/部署拓扑与环境.md)。
- **已收口回合对迟到端不可见**：无 live run 时 `follow=false` 回 204、`follow=true` 只挂空闲心跳；历史走 REST 消息窗重载，不由事件流补。故**消费方义务**：重连后若确认空闲，须自行拉一次消息窗对账，否则断线期间另一端整跑完的回合在本端永不出现。三端各按自己的重连形态挂：桌面 `reconcileAfterReconnect`（静默退避，重连后空段即补）；手机 `planFollowIdle`（横幅手动重连 / 回前台解冻 / 服务端关流后自动重挂三处触发）；IM firehose 同款 `catchUp`。
- **迟到接入者看到成品、不是打字过程**：delta 类是 DERIVED 不落 journal，process 车道是语义边界步粒度，重放把它们还原成整块。逐 delta 节奏只有 dev 录制器保留（见下节）。
- **慢消费者丢的是流畅度、不是正确性**：每订阅者一条 1000 帧有界队列，积压满即弃最旧帧并记 `event_sink.backpressure_drop`（同 IM firehose 口径）。事实源是 journal——重连整段重放即补齐，故丢帧不构成数据缺失。真正留下缺口的只有「卡到积压上千帧、之后又自行恢复且始终不重连」的连接，本端不知道自己丢过帧（`dropped` 只在服务端）。排期时按流畅度问题看待，勿当作静默丢正文。

### ⏳ 目标：多人同看

已确认方向是**多人同看同一任务**（协作 / 围观），非仅同账号多设备。三个前置彼此独立、可分别定案：

1. **跨进程事件总线**——取代进程内单例，同时解掉多 worker 拒启。
2. **「谁能看这个会话」的授权模型**——✅ 文件夹成员可打开桌上对话（可编辑续跑/跟播/审批/停止；只读只看）；外人仍 404、不泄漏存在性。不复用已废的共享空间盘。权威 → [工作区 · §八、协作桌（文件夹成员）](/docs/02-架构/双模式工作区.md)。跨进程总线与订阅配额仍是扩容（本表 1、3）。
3. **订阅配额**——现状订阅数与 SSE 连接数均无上限，每条占一份队列 + 一次全量重放，多人场景放大该风险。

## 四、录制与回放

**回放 = 同一 SSE 事件契约的另一种事件源**，不是另一条执行链路。业务只认事件契约；执行语义只有 runtime 一份。

| 层 | 职责 |
|---|---|
| 录制 | EventSink 纯 tap（失败不影响回合，默认关） |
| 事件文档 | 线上契约超集；录制永不带 `projected` |
| 裁切 | durable-face → 脱敏 → golden |
| 回放 | A=FOLD（不 remint）与 B=SINK（可 remint）互斥 |

**红线**：回放/演示不得侵入 runtime 语义（禁 `if is_demo_tape` 改语义）。有副作用的客户端工具导出期硬剪。操作 → [`demos/README.md`](/demos/README.md)。

**边界**：不做全站 event sourcing；与生产 attach/重连（`Last-Event-ID`）正交不合并。**否决**：有副作用的服务端工具短路回放——导出期硬剪已够，短路会把回放语义漏进 runtime（非 ⏳ 待落地项，不再排期）。
