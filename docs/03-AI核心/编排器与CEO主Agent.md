---
status: landed
code: apps/server/agentcore/
related:
  - docs/03-AI核心/运行时总览.md
  - docs/03-AI核心/执行引擎架构设计.md
  - docs/03-AI核心/检查点与开工卡.md
  - docs/03-AI核心/上下文工程.md
  - docs/05-平台与运维/平台LLM接入.md
skip_if:
  - 只改检查点卡片 UX（读检查点 / 前端UX）
---

# 编排器与 CEO 主 Agent

> **权威范围**：CEO 定位、职责边界、路由 / 团队形态 / 认知分工判据、**具名 playbook 准入与名单**、关键字段语义、冷启动探索幕**的编排流程**（触发条件权威在 [记忆 · 探索触发](/docs/03-AI核心/Agent记忆与知识系统.md)）、`replan`。检查点 → [检查点](/docs/03-AI核心/检查点与开工卡.md)。实现细节 → 见代码: `apps/server/agentcore/runtime/`。
>
> **主循环归属**：接住「**说**」（唯一对话入口）并实现「**组**」（判轻重、自动组队）→ [主循环](/docs/01-产品/产品定位与品牌.md)。

## 核心定位

编排能力归属会话型 **CEO 主 Agent**：唯一对话入口与声音，也是团队规划大脑。用户是老板；CEO 受雇掌管团队、对其负责——关键岔路请示、收尾汇报。确需团队时经 `delegate` 下达子任务，执行引擎调度 worker，CEO **用自己的声音**收尾。

CEO 是**管理者**，也是判断者：要不要拉人由他判（模型自判，常驻核不写编号闭集）。持已装配干活工具，与工人同形（写盘 / `run` 短·验证·长驻 / `host` 全 action / `browser` 含 screenshot / git 写入 / 对话日志 / `desktop_notify`）；超规模仍自判 `delegate`。闲聊 / 窗口已有证据则自己开口；默认交给团队（协调和收口），自己做只限短答和单点；成件事交团队。成规模查证、要并行、实质讨论、成篇或可运行应用——交给团队。

底线：对用户呈现**一个 CEO 声音**；轻量闲聊 / 单点直答 / 窗口里已有证据的小落盘与纯启服（零编排开销）；超规模走团队。

### 职责边界

| ✅ CEO 做 | ❌ CEO 不做 |
|---|---|
| 与用户对话、来回澄清；判断开口前该有的工作区证据要先取 | 亲自串行跑成规模取证 / 成篇长文 / 可运行应用（交给团队） |
| 轻量直答（一两处文件 / 一条事实）；窗口里已有证据的小落盘自己写 | 为短答和单点支付规划税 |
| 已装配时写 / 改 / 删文件（GRANTABLE，走同一 ApprovalGate） | 未装配执行面却假装能跑 |
| 已装配时纯启服 / 重启 / 看长驻是否活着（`run`，云桌/本机） | 自己摸完整场成规模调查 |
| 开工前探路（定位入口，不收集结论）；团队跑完写简短概览 | 复述各 worker 全文（细节由前端 run / 图视图展示） |
| 理解意图、拆任务、定角色与依赖（`depends_on`） | 持 `escalate` / `handoff`（仍仅工人通道） |

**探路 vs 摸底**（编排 skill / eval，不是常驻核判决树，不是引擎硬闸）：探路只回答「从哪几个入口进」。停手条件是认识上的——能点名入口且能写下目标·约束·验收、执行层可留给工人——不是轮次配额。判断 / 方案若取决于工作区现行文或代码、窗口里还没有 → 先取证再开口（取证可以是自己读几份相关文件，不等于必须开组）。窗口里入口已在 → 探路已结束。成规模取证 = 摸底。**组队靠 CEO 自判 + 工具 description 短触发**；引擎不剥调查工具、不丢闸后长文。引擎不扫用户原文猜意图。细则 → `consult(team_orchestration_advanced)`【工作流】。

工具结构分界：builtin 干活工具（读 / 写 / 跑，含 action）CEO+worker 同形，审批只认 `ToolSchema.approval`（GRANTABLE 走同一 `ApprovalGate`，不因角色另造闸）。**仍仅 worker** = `escalate` / `handoff`（工人通道）。MCP：CEO 可持，开场不灌 schema、`consult` 成族晋升（与工人同闸）。**`run`** schema `GRANTABLE`。**`host`** schema `NEVER`、CEO 可持，GRANTABLE action 运行时升 `host` 轴。动作表 → [工具 · Host](/docs/03-AI核心/工具与能力系统.md)。自研编排（否决 LangGraph / CrewAI 等）：编排是核心壁垒，须完全掌控。聊天优先 + 按需编排（否决「编排器唯一入口」——每条消息付编排税）。

**档位取舍**：工具面取档 1（CEO 持干活工具）+ 路由仍自判（身份 + 工具 description 短触发；编制细则在 skill / eval，不进常驻核判决树）。曾否决档 1 全能 CEO 后因编排税重开工具面；现干活面（含 `run` / `host` L3 / git 写 / 截图 / 对话日志 / `desktop_notify` / MCP 按需）与工人同形，不再用角色硬闸卸权。复利仍在（大文件进 CEO 窗此后每回合都付），用规模路由守团队。**否决**把 `escalate` / `handoff` 灌进 CEO。**否决**把 MCP schema 灌进开场 FC 表。**否决**再立搬家工具当收口仪式 → [术语表 · 成品归位](/docs/01-产品/术语表.md)。系统收口第二轮把同一结论重讲一遍已删 → [执行引擎 · 团队终态](/docs/03-AI核心/执行引擎架构设计.md)。

## 路由 / 团队 / 认知分工

发问优先：先判是否**挡路**（桌上结果未钉且猜错会做错），再判规模。挡路 → `ask_user` 短澄清（可穿插探路）；仅可逆低杠杆才标假设继续。产品原则全文 → [检查点与开工卡 · §一 · 挡路拍板](/docs/03-AI核心/检查点与开工卡.md)；无开场提案/场面硬账。闲聊自己回；窗口里已有证据的小落盘自己写；超规模交团队。**规格已齐立刻派**；能力盖不住才短问载体，次优标假设继续——见下节。

| 判据 | 结论 |
|---|---|
| **直答** | 闲聊 / 窗口已有证据的追问、单点确认、读已知少量文件、聊天里短文或短改写、窗口里已有证据的小落盘、开工前探路（只定位入口）；判断若取决于工作区文件且窗口里还没有 → 先取证再开口（取证≠必须开组）；**本回合能力问答对照能力行**（已装配则 `consult`/短探，禁止用邻格未装配写否决论文；权威 → [上下文工程](/docs/03-AI核心/上下文工程.md)）；**已装配时纯启服 / 重启 / 看长驻是否活着**（`run`，云桌/本机；勿为此派 `runtime_ready` 批） |
| **委派** | **必须**（规模）：成篇落盘、可运行应用、成规模取证（横扫多来源、自己会连搜收齐）、要并行、要交叉验证、对照行业或点名多方案、同一问里多块实质讨论。讨论 / 先不成文 ≠ 自己做完。**不是**「有落盘就必须派」——一行 / 一处且窗口里已有证据 → 自己写。几人见下行 |
| **团队形态** | 成件事交团队；按活的结构组队，人数不是优化目标。可并行就并行，要交叉验证就独立审。点名对比 N 个对象 → 至少 N 人（同一话题多个切面 ≠ N）。1 人只在活本身就是一块（修一处、短文、窗口已有证据的小落盘）。勿按工种头衔凑满。广度调查按结构编制，task 点明「回报精炼结论」。有结构的活就派、该落盘就落盘。仅把「论文/开源」当资料源 ≠ 要成文。可提交长文 / 用户点名审校才上 `cite_write_review` 满编；普通构想不默认学术审校。规格已齐或卡已结算 → 立刻派，勿把「答完澄清」做成默认 `end_turn`。闲聊自己回；干活默认派。猜错会做错才短问（原则 → [检查点 · 挡路拍板](/docs/03-AI核心/检查点与开工卡.md)）。公共事件按结构组队（异质透镜并行 + 汇总）；点名开辩 → `debate`。上游注入下游由引擎保真（有文件递路径）。糊「做个网站」只消歧展示页 / 工具壳 / 业务应用。ask `label` 只写桌上结果，不映射编制套餐。做软件无专段 HOW，编制交给模型。→ 见代码: `runtime/skills/team_orchestration.py`（编制 HOW；常驻核不写判决树）。 |
| **认知分工** | 约束归 CEO、专业方案归专家；task 只写【目标·约束·验收】；编制按活的结构、不按话题套默认人数、人数不是优化目标；摸底方向一句目标，不列书单/大纲；`contract` 是验收契约非结构蓝图；审查类「重点关注」进 `team_brief`，勿写进 task 替 worker 作答 |

短文分界：未要求存文件的短文 / 短改写、窗口里已有证据的短追问与小落盘 → 自己写；缺工作区证据 → 先取证；成篇 / 可运行应用 / 其余超规模干活默认派；1 人只在活本身是一块。CEO 绝不为省委派把整份代码贴进正文。

### 载体/手段

目标跟用户。点名载体但**能力盖不住** → 一次短 `ask_user`：第一句说清做不到什么，再给替代；用户坚持 → **零摩擦**按点名开做。明显次优 → 回复里标假设继续，不必先停。勿扩成开场场面账；合理点名仍立刻派。

仅提示词 / Skill（拦截阶梯 1；→ 见代码: `runtime/resolve/prompt/` · `asking_the_user`）；**禁**硬闸、**禁**意图分类器扫文猜「该不该换载体」、禁复活场面硬账（权威 → [检查点 · §一](/docs/03-AI核心/检查点与开工卡.md)）。提示词只写可复用原则，**禁**为单次失败 case 写话术剧本（具体回归放 conformance）。

**部分材料明示范围**：用户附材料并收窄为本轮附件 / 工作区已有产物时，须先对照动手（缺口分析或改一版）；缺整仓只说明局限与单点缺件——禁止整轮只催源码。与打开本地项目正交（开项目=换工程面，非开工前置）。

**编制**：成件事交团队；按活的结构组队，人数不是优化目标。1 人只在活本身是一块。勿按工种头衔凑满。「按缝拆、能少则少 / 跨域合成少派」已撤销（与 Multi-Agent 优势最大化冲突）。评测跑法 → [本地开发 · evals](/docs/02-架构/本地开发.md)。

## `delegate` / `replan`

`delegate` 默认**非终态**：worker 跑完交回 CEO，CEO 写简短概览收尾（否决独立 SYNTHESIS 合稿节点；单 worker 成功亦然）。曾有 `finalize=true` 单人直出（HANDOFF 当回合答复、省合成轮），与「一个 CEO 声音」冲突，已撤。图由 CEO 在 ReAct 循环里增量声明——非外部一次性 JSON 计划。**参数主路**：默认手写顶层 `tasks`；具名 `playbook` = 固化流水线快捷套餐（与 tasks XOR，禁同时有内容）。准入与现行名单 → 下文「具名 playbook」。

**跨文件夹（✅）**：跨已登记文件夹一律 `delegate` 各填 `target_folder_id`（写不写盘由 write_scope/grant 正交）；CEO 的 `list_folder_dir` / `read_folder_file` 仅派前轻量认桌；均不改会话 `folder_id`。裸聊建桌、剥壳、Composer 三选、本机传统 → [工作区 · §五、绑定：文件夹即工作区 · 双通道入口](/docs/02-架构/双模式工作区.md)。

| 动作 | 语义 |
|---|---|
| 一次塞 N 个 task | 全景计划（一批声明完整分工） |
| 同回合再调 `delegate` | 并入**【同一张】**协作图（同 `execution_id`）；协调中经 `live_plan`、本回合上一张图经自动合入，均可在 `build_run_plan` 前解析宿主节点；`depends_on` 可填本批 / 宿主声明 `id`、无歧义角色名。开辩不走这条：`debate` 独立成图，不链本回合调研宿主、不把调研员拉进场当证人。 |
| 跨回合延续 | 仍由模型显式表达「接着上一支团队干」（`append_to_execution_id` **只填 `"latest"`**，引擎解析到最近一张；模型侧拿不到图 id——当轮 `run_id` 在 delegate 回执「队员终态名册」，不常驻系统尾），语义是**新开一张锚在本回合的图 + 系统写 `prev_execution_id` 链回去**，不把新人塞进旧图。**判据是回合边界，不是上一张图死没死**：上一张仍在后台跑时同样新开——`adopt_active_execution` 只把本回合绑上那条 live execution 供 `wait` / `cancel_worker` / 插话路由，派单落图一律读本回合 mint 的 `execution_id`；让 adopt 顺带决定图归属，等于「上一轮碰巧还没跑完」就静默改变新人去哪张图。**已废**复用旧图继续生长（辩论幕挂调研宿主同废；开辩一律独立成图）——生长回流到已收口的旧宿主会让图锚错回合、进度分母吃旧节点、journal 因宿主回合已死被丢。**否决**「无参数自动链上一张」：同对话里毫不相干的新团队会被误判成延续。团队延续读图链，**否决**为此另建 team 实体（roster 仍只管同人续派的现场） |
| 并行度 | 由节点 `depends_on` 数据声明（无依赖即同波并行），非靠模型并行 tool call |
| 任务 `id` | flat / DAG 均保留声明 `id`（铸 `{prefix}_{raw}`）；未声明 flat 仍用序号。跨批依赖靠声明 id 或角色名，勿臆造未声明短 id |

**`replan`**（波边界续跑，与 `delegate` 正交）：队员 `escalate kind=scope`、或内部/套餐留下的待定稿步就绪时，调度器在决策边界让出；CEO 定稿 / 纠偏后续跑**同一张 DAG**。手写「下游还没想好」→ 先跑上游，再 `replan(add)` / 再 `delegate`，不在派工表上留坑。

| 参数 | 要点 |
|---|---|
| `binds` | 据上游产出把占位节点定稿（role / task / deliverable） |
| `steers` | 给尚未运行的下游追加操舵；已完成步骤不可操舵 |
| `add` | 追加计划外新节点（拓扑校验；未知依赖 / 成环等整批拒绝） |
| `stop` | 未跑步骤 SKIPPED，已完成产出交回 CEO 收尾 |

`binds+steers+add` 先全量校验，任一非法 → 整批拒绝、暂停计划零改动。否决把 `delegate` 重载成「续跑旧计划」入口；带现场续派另见 [多轮编排与同人续派](/docs/03-AI核心/多轮编排与同人续派.md)。

**成品归位已撤。** 过程稿抽屉 `工作稿/`（裸文件名 / 不知放哪）；用户要拿走的文件在派单时用 `form=workspace` / 显式 `artifacts` 钉路径。经理不要发明路径。→ [术语表 · 成品归位](/docs/01-产品/术语表.md)。

协调模式（根 CEO；**含单 worker**）：默认后台跑、CEO 继续 ReAct——CEO **不可**再勾阻塞。同步阻塞只剩引擎路径：嵌套 lead / 成篇套餐提纲把关 / 画布人工把关。单 worker 也进协调，是为让用户插话在派单期可达（阻塞路径下 CEO 把执行权交给了 worker，插话读到也无从响应）并让 CEO 手上有 `cancel_worker`；编制到即开跑，单 worker 的零摩擦外观不因此变重。结构跟着证据走：调研成篇用 `depends_on` 把「定结构」摆到调研之后，用户明文看提纲才把关（`cite_write_review` 且 `checkpoint=true`，或先派再问）。委派后用团队产出写综述（提示强化，非硬禁只读）。worker 协作通道 → [Agent 协作模式](/docs/03-AI核心/Agent协作模式.md)。协调 `wait` 在用户侧热审批/授权未决时禁止空等假装推进，应报告阻塞（等你允许、点名工具）后听团，队还在；引擎不得把未决允许卡收成整队取消 → 见代码 `has_hot_user_pending`。用户显式停止 / regenerate 会 orphan 热交互并写入 journal（取活 turn 的 `message_id` 作 `turn_id`，非路径上的用户消息 id）。**例行成功完成不叫醒 CEO**（调度与协作图已自己往下走）；**失败立刻叫醒**（还可补人），跳过/取消挂在失败或全员完成/整队取消上，不单独一轮。**协调期 CEO 可见面纪律**：无新结论可静默（`wait` description 唯一所有者，不进每批注入）；禁止用用户可见 content 复述「谁还在跑」类进度（协作图是进度真相）；开口仅请示 / 报告阻塞与选项 / 宣布阶段结论 / 回应中途插话；插话注入只带原文 + 一句先开口 / 独立新活走 `queue_user_message(interjection_id)`；`update_synthesis` 禁纯进度播报；协调态进度旁白经 `deliverable_only` 不进终稿 `messages.content`（过程仍进 process）。

### 批次入闸 vs 回合收敛（边界）

`drive` / `replan` **开工前**的批次门控（`post_close_gate`：收口后冷开整团重派硬拒；`channel_dead_gate`：通道死且需写桌则硬拒，任何参数都不逃生；`completion` / `supervised`：能力·冷启动软检与补跑合入）与 engine 回合内收敛门控**分轨**——权威总述 → [执行引擎 · 治理门控双轨](/docs/03-AI核心/执行引擎架构设计.md#治理门控双轨)。新闸：能拒整批开工的挂 delegate；只影响 ReAct 继续/丢稿的挂 engine。

### 同队续派 = 一等入口 {#同队续派一等入口}

批次一收口，协调会话不再活跃、`replan` 也不在（它只活在受监督态），「给同一支团队补跑 / 接着干」于是只剩会被冷开闸拦下的冷派——**缺的是入口，不是模型服从度**。

同队续派是一等入口：闸**按结构路由**，不要求模型改写参数才能过。`post_close_gate` 先把批次拆三堆（只看结构字段，**不扫 task 自由文、不做意图分类**）：

| 堆 | 判据 | 入闸 |
|---|---|---|
| **续派** | `continue_from_run_id` 指向非缺口 run | 不进闸、**不限条数**（名册与作者链 `recall_count` 自然兜底；目标现场存不存在由续派执行层逐节点如实拒） |
| **补缺口** | `replaces_run_id`，或 `continue_from` 指向 FAILED/SKIPPED | 仍按 `MAX_GAP_FILL_ADDS` 限流（与同图 `replan` 补跑闸同判定） |
| **冷开** | 两者皆无 | 只对**这一堆**判 substantial 大扇出并拒 |

配套：当轮 delegate 回执名册给出 `run_id`；收口后冷开拒收口可附候选，**不**每回合注入 `<近期团队图>`。**否决**为此新增工具 / 新增与 `append_to_execution_id` 语义重叠的参数：入口就是 `delegate.tasks[]` 上的两个既有结构字段。→ 见代码: `runtime/delegate/team_continuation.py`

#### 闸无模型跳过

CEO 契约没有顶层 `force`，闸没有模型可填的跳过键，解析层也不把旧布尔或闸名数组当放行。同队续派 / 补缺口只走 `continue_from_run_id` / `replaces_run_id`（上表三堆）；同构再派、触顶换马甲、座位重叠同样无跳过——触顶的唯一出路是对该 run 设 `continue_from_run_id`。通道死属能力缺失，填任何参数都不逃生。

旧一键全开 / 逐闸数组已撤，因把模型逼向跳闸。

**触顶换马甲闸的记忆有窗**：闸给的唯一出路是对那个 run 设 `continue_from_run_id` 带现场续派，而现场活在留人名册里，所以闸的对话级记忆与名册**同寿**——按记录入册时间过 idle TTL 即失效，进程内再按对话 LRU 淘汰。**否决**进程内永驻名册（会把谁也用不上的旧记录一直拦住同主题新人）。回收只做减法：判据、相似度口径与拒绝文案一概不动，过期只让闸**更少**开火。→ 见代码: `runtime/coordination/thrash.py`

### 执行写路径 vs 进度读视图

| 面 | 职责 | 禁止 |
|---|---|---|
| **`drive*`**（`drive` / `drive_coordinated` + setup/preview/finalize/terminal/redirect） | 派发与执行**写路径**：建图、跑批、收口记账 | 为「好看」改写协作图投影语义冒充执行真相 |
| **`CoordinationSession.live_plan`** | 协调态执行真相（由 supervised / host / session 恢复写入） | 经只读投影回写 |
| **`pipeline_view`** | 只读进度投影，注入 CEO 可见面 | 当作第二写路径 |
| **`isomorphic`（+ thrash）** | **drive 入闸**（拒同构再派 / 拒触顶换马甲），不是 UI fold | 与前端图折叠混为一谈 |
| **前端协作图** | SSE → `projectExecution` 等**读投影** | 反向充当执行权威 |

**不把** drive 事件流合成进协作图通道（合成列观察项）。写只走 drive / session；读视图与 UI 只派生。本表是该分工的**唯一权威**（协作模式 / 协作图 UX 只留短指针）→ [协作图 UX](/docs/04-前端/协作图与双视图UX.md)

收尾：先对账拼图边（4b：冲突 / 缺口 / 重复）→ 核验原始目标（4a：完工判定）→ 写概览；未达成就续派 / `replan`，别假装收工。`playbook`：默选手写顶层 `tasks`；具名本只冻「形状就是活」的骨架（名单与准入 → 下文「具名 playbook」）。做软件 / 建站 / 工具台 / 点名对比 / 局部单功能一律手写——**已删**具名 `build_app` / `build_website` / `build_website_verify` / `compare_options` / `build_feature`。做软件无专段 HOW。多角摸清按活的结构编制（有独立块才 `map_fanout`；讨论对齐不发卡，干活默认派、收口仍 CEO 写），正式长文成文专线 `cite_write_review`（点选成文≠立刻满编；普通构想不默认学术审校）。代码审查手写 → `consult(team_orchestration_advanced)`。**Agent/自动化**不靠场面硬账；缺形态且挡住编制时 `ask_user` 短问（label 写桌上结果，不设讨论类开场菜单；桌上结果已是对话本身则不发卡；糊「做个网站」须消歧展示页/工具壳/业务应用，禁编制档 / 禁具名建站套餐），由模型自洽选择交付路径 → [检查点与开工卡 · §一 · 挡路拍板](/docs/03-AI核心/检查点与开工卡.md)。用户点名开辩才走 `debate` → [辩论编排设计](/docs/03-AI核心/辩论编排设计.md)。

提示词怎么写（宪法非法例、一层一所有者、事故入场闸；工作纪律分层见所有者表）→ [上下文工程 · 提示词设计原则](/docs/03-AI核心/上下文工程.md#提示词设计原则)。禁止为读规则再派 worker。失败工具回执留在历史，不另注易变尾。`delivery_status` 仍 stamp 同轮对账（合回正品 / 同轮档位），不抄进下一轮提示。

## 具名 playbook {#具名-playbook}

> 服务主循环「**组**」：老板不选流程、不填表单。CEO 默选手写编制；只有冻死还值的流水线才进名单。

**主路**是手写顶层 `tasks`。具名 `playbook` 与 `tasks` XOR：点名一本 + 填槽，引擎展开成 `build_run_plan` 已能消费的 tasks 数组——同一条执行管线，不加子系统。不是通用模板引擎，也不是老板的流程菜单。工具箱官方模板是 **`PLAYBOOKS` 的精选子集**（现 `map_fanout` / `cite_write_review`），不是 1:1；窄协议 / 恢复 / 续派形状留在 CEO 运行时词汇。

### 价值原则（形状就是活）

具名 playbook 只冻**这件事本身的正确做法**：换主题后节点 / 角色 / 合同仍一样，且漏一步活就不成立。

| 真价值 | 假价值（禁止当准入） |
|---|---|
| 形状就是活（独立审校、必须验证、摸清禁止升成文） | 给一个领域发标准班子 / 默认文件图纸 / 工种流水线 |
| 内部合同手写抄不便宜（结构闸、引用时序、命题卡） | 把领域名当总路标（「做软件 → 套本」） |
| 减法：冻住不要多出来的步 | 协作图好看、规定交付必须/不许某种文件形态 |

检验：真实 AI 开发（已有仓库上改、先对齐再按方案做第一期、一句话从零做）里，这本是在执行用户的活，还是覆盖成工厂图纸？覆盖 → 不进名单。技能可教 HOW（如 `consult(team_orchestration_advanced)`），**不等于**登记具名流水线。

### 准入（加本必须同时满足；过不了 → 手写或技能）

1. **拓扑固定**：除同类扇出超限折叠外，不按调用改节点数 / 角色。结构分叉退回手写。
2. **内部合同手写抄不便宜**：结构闸、引用时序、命题卡、必填验收槽。只有 task 文案不同 → 不算。
3. **高频，且 CEO 手写经常写错形状**（漏审校、漏 `verify`、一人包办 N 对象）。
4. **与现有名字正交**：同拓扑只差默认槽 / 落盘文案 → 合并进槽，或删较弱的那本。
5. **不拿场景当名字**：「调研 / 审计 / 修码 / 做软件」是桌上结果或技能；playbook 名的是流水线形状。
6. **通过「形状就是活」检验**（上节）。领域工厂图纸过不了第 5–6 条。

**否决**：按场景加本（建站、做软件、对比选型、单功能交付）；把 brief / 对比 / 多透镜合成一本再加 `kind=` 槽（分叉藏进一名）；扫用户原文猜该用哪本；把 `debate` 收进 `PLAYBOOKS`（它已是另一条确定性骨架）。

### 现行名单

id 标流水线形状，不标桌上结果。**不设别名**，旧名与未知名同处理。

| 名字 | 形状 | 审计 |
|---|---|---|
| `cite_write_review` | 取证→提纲→成文→独立审校；成篇硬门只认此名；提纲关默认不停（明文才 `checkpoint=true`） | 留。形状就是正式长文 |
| `map_fanout` | N 路并行一页地图、无合成/审校节点；防误升成文专线 | 留。价值在减法 |

**已删**：`code_audit`（审查是桌上结果，与 `map_fanout` 同拓扑；内部 JSON 台账 + 五章标题闸过不了准入。HOW 留 `consult(team_orchestration_advanced)`）；`lens_crosscheck`（与手写「N 异质透镜 + 汇总交叉核验」同拓扑，场景包装不是合同；HOW 留 `consult(team_orchestration_advanced)`）；`diagnose_fix_verify`（单症状修码一人够；独立验证不是修码保底编制）；`build_app`（Vue/SPA 工厂图纸，不是「做软件」；真实开发会覆盖用户方案）；`compare_options`（与手写「点名对比 ≥N 人」同拓扑）；`build_feature`（前后端并行 = 技能「契约共享面」，且 `include[]` 分叉）；更早的 `build_website` / `build_website_verify`（建站套餐；现手写）。共享口径只走非空 `team_brief` 或短规格岗，不因具名本另开边信道。

**现行钉死**：上表两本全留；`debate` 不进 `PLAYBOOKS`。加本仍过本节准入。

→ 见代码: `runtime/runs/playbooks/` · `workflows/playbook_templates.py`

## 关键字段语义（摘要）

| 字段 / 概念 | 语义要点 |
|---|---|
| `depends_on` | 并行 / 串行的唯一开关；空 = 可立即并行；调度器据依赖定并行度。同回合二次委派解析范围 = 本批 ∪ 宿主图（活跃 `live_plan` 或本回合上一张图）；失败回执列可用节点 + 可执行下一步（角色名 / id） |
| ~~`require_upstream`~~ | ✅ **CEO 不填**。默认 ≥1 上游成功即跑；缺席标前置缺席；零成功才跳过。严格级联仅内部/多余键。 |
| ~~`result_handling`~~ | ✅ **CEO 不填**。有落盘递路径；散文默认全文、过长引擎裁。不作用于 CEO 综述。→ [Agent 协作模式 · handoff](/docs/03-AI核心/Agent协作模式.md) |
| ~~`complexity_hint`~~ | ✅ **CEO 不填**。单人只交文字时引擎可自判轻；不映射 worker token/超时 |
| ~~`coordination`~~ | ✅ **CEO 不填**。权威 → [Agent 协作模式](/docs/03-AI核心/Agent协作模式.md) |
| `deliverable` | **✅ 派活单三档**：`form` = `prose`（看）/ `files`（存文档，漏填即此）/ `workspace`（改工程，吞 `workspace_native`）；可选 `artifacts`（仍可选；CEO 默认省略、工人自起名；仅用户点名或流水线/画布写死才填，不扫 task 自由文、不发明文件名）。节点上永远有 Deliverable。`form=prose` 不得同时声明非空 `artifacts`（硬拒）。CEO/`replan` schema 只暴露 `form`+`artifacts`；章节 / JSON / `strict` / `artifact_dir` / 引用闸等仍可解析、不进填参面。**`form` 只表交付形态，不再代理探索期「别乱写工程」、也不再硬卸写工具**。零写 / 结构不达标都不把队员打 FAILED（仍 COMPLETED，缺口进提醒）；`strict` 仍可解析、不翻成败。收工用户面认实际落盘，不以声明路径当白名单（声明命中时备份仍不进清单）；路径验收 HOW → [执行引擎](/docs/03-AI核心/执行引擎架构设计.md)。✅ 任何成功写盘都算产品落盘（含 `research/` 等中间笔记；`artifacts` 不再门控是否计入）→ 见代码: `runtime/runs/landing_product.py`。**数据文件整理且无执行**：完整交付 = 原件结构报告 + 待跑变换脚本（两份写进 `task`，不必钉 `artifacts`）+ 一句「运算环境暂时不可用，稍后再试」——这是完成态，不是「表的缺口」；禁止手抄 csv 顶替、禁止让用户绑本机文件夹。硬缺口 `no_exec_table` 的前提是本回合存在 worker 无法可靠解析的源数据文件（附件 / 工作区源文件的类型信号，不扫正文、不靠文件名）；数据内联在消息里、无此类源文件时落 csv/xlsx 不是缺口。否决加闸扫收口话术。→ skill `data_file_landing`。不再有按验收 kind 的队形闸。已删 `requires_files` / `name` / `must_contain` / `min_length` / `must_contain_soft`（见下节） |
| `write_scope` ✅ | worker 本批可写范围：`none` / `explore_memory`（仅 `AgentCore/` 约定记忆与探索笔记）/ `project`（用户工程树，默认满权限批次）。探索硬挡 pending 时上限 `explore_memory`；越权在**写工具层**拒，不在 `delegate` 入口因 `form=files` 拒整批。否决：explore 专用 playbook 分叉、pending 时静默把 files 改成 prose |
| `continue_from_run_id` | 带现场续派；权威 → [多轮编排与同人续派](/docs/03-AI核心/多轮编排与同人续派.md) |
| worker 模型 ✅ | 每 worker **节点/run** 可选显式模型身份（与辩论辩手身份同族）；**省略** = Worker 槽（空则 follow 主）。CEO/`tasks[]` 节点显式仍有效；人不在确认面改模。定案全文 ↓ |

嵌套委派：硬上限 `depth≤3`（合法链 CEO → depth1 → depth2 → depth3 叶子；depth&lt;3 默认获 `delegate`，`replan` 在已有子计划后挂上），无 `can_delegate` 字段。worker 工具集缺省全量（内部装配）；CEO **不必也不应**手填 `tasks[].tools` 收窄（填了也不生效）。depth=1 captain 对路径 B 形 brief（成果级目标·约束·验收、本轮无结构钉成单切片）→ **优先**先再 `delegate` 补编制再整合（优先级 nudge，非硬流程、非「未嵌套禁写」）；豁免：单文件 / 已钉薄壳 / 强耦合同 run 切片 / 小修·机械单步。整里程碑 M0 / 空仓多模块骨架不在豁免内。**禁止**「凡大活必嵌套」；失败只回退提示词，不升级闸。根 CEO 编制 HOW → `consult(team_orchestration_advanced)`；嵌套 lead → `consult(lead_subteam)`（队员专属、持 `delegate` 才进目录与 fetch）。两把 `delegate` 的 description 分叉：根侧协调立即返回 / 嵌套阻塞等到子队收工。

### 交付验收：无 kind 枚举、无按 kind 硬挡 {#交付验收}

schema **没有** `completion_criteria` 字段。引擎不按验收标签硬判完成。错收工接盘 = CEO 提示词复盘 + deliverable / contract + **用户面以磁盘为准**（队员零写 / 结构不达标仍 COMPLETED，见下）+ 人审。省略即常态。工具面 `run` 内部仍分短执行 / 验证 / 长驻三类，与验收标签正交。→ [工具 · 执行三分](/docs/03-AI核心/工具与能力系统.md)。

**否决**领域 kind 扩表；**否决**边删边加新启发式完成硬闸；**否决**把「验码绿」等再伪装成新 kind。→ 见代码: `runtime/delegate/completion.py` + `tools/builtin/delegate/schema.py`

### 交付契约：CEO 填参面不含的字段

CEO/`replan` 可见契约**没有**：`playbook_none_reason`、`deliverable.name`、`tasks[].objective`、`must_contain`、`min_length`、`requires_files`、`must_contain_soft`。**禁止回潮**。

**写盘只认** `form=files` / `form=workspace` / 非空 `artifacts`（漏填=`files`）；目标语义并入 `task`。

**成篇硬门**：只认具名 `playbook=cite_write_review`；**否决**字数结构腿与扫 task 自由文补门。handoff 正文地板 = 非空（不暴露 CEO 字数旋钮）。

**边界**：playbook 内部仍可留 `artifact_dir` / `required_sections` / `output_format` / `citation_mode`；`strict` 仍可解析、不把节点打 FAILED。`write_scope` 不变。CEO 填参面是三档 + `artifacts`。→ 见代码: `tools/builtin/delegate/schema.py` + `runtime/runs/types.py` + `runtime/runs/contract.py`

### 交付物派活单三档

CEO 派活只声明写意图，不设计验收单。队员看不见用户原话，**禁止**靠自判决定产物地位。

**CEO / 画布可见**（仅此）：`form` = `prose`（看 / 纯文字）/ `files`（存文档）/ `workspace`（改用户工程树，吞掉 `workspace_native`）；可选 `artifacts`（仍可选；CEO 默认省略、工人自起名；仅用户点名或流水线/画布写死才填，不扫 task 自由文、不发明文件名）。画布交付形式是三选一（纯文字 / 文档 / 改工程），默认文档。收工用户面认实际落盘，不以声明路径当白名单（声明命中时备份仍不进清单）——路径验收 HOW → [执行引擎](/docs/03-AI核心/执行引擎架构设计.md)。

**省略**：无对象 / 空对象 / 漏填或非法 `form` → `files`。`prose` / `workspace` 必须显式。打招呼等「只看」靠 CEO 勾 `prose`。入参 `workspace_native` 且非 prose → 升 `workspace`；prose 清 native。`workspace` leftover `artifact_dir` 清掉，不拧进工作稿。

**离开 CEO 填参、固定流程内部可留**：章节验收 / JSON 形态 / `artifact_dir` 常量 / 两阶段引用。`strict` 仍可解析、不把节点打 FAILED。

开场「交付物规格」只列本节点实例（有声明才渲染「交付路径」、非工作稿落点目录、必须章节名）；无实例则省略整块。交法（看 / 存文档 / 改工程）在队员身份；过程稿抽屉在工作区事实行。检索预算不进此块。

**`write_scope` 不在本契约改。** 闲聊第一次真写再给桌子。

**否决**：派活单不声明、队员自判 + 收工对账；扫 role·task 自由文猜看/存；把质检字段再露回 CEO schema；为漏填 `prose` 去扫用户原话补档；**连 playbook 内部验收一并拆掉**（见下「砍更狠」）；把零写 / 结构不达标升硬失败。→ 见代码: `tools/builtin/delegate/schema.py` · `runtime/runs/types.py` · `runtime/runs/builder.py` · `runtime/runs/artifact_dir.py` · `runtime/runs/executor/identities.py` · 画布 `WorkflowNodeInspector.tsx`

**设计原则**（Why；跟代码对不上的才写在这里）：编排者声明**产物形态**；落点只在已有名字时钉，工人自起名是默认。结构化验收写在具名流水线 / 合同代码里，不交给经理模型现场设计 QA 表。工人看不见用户原话，所以「看 / 存文档 / 改工程」必须是结构字段——这是我们和行业的唯一硬差别，不能学别人把形态留给工人自判。

**行业对照**（钉原则，不是跟风清单）：CrewAI 任务是人写的 `description` + 一段 `expected_output` 自然语言，可选 `output_file`（≈ 我们的 `artifacts`）；`output_json` / Pydantic / guardrail 是**开发者代码**，不是经理 Agent 填的验收对象。LangGraph 节点写进开发者定的 typed state。Cursor / Claude Code / Codex 任务是自然语言，真相是磁盘文件 + 测试 / 人审。OpenAI Agents 的 `output_type` 是开发者预置的响应 schema。共同点：**验收旋钮不进编排 LLM 的工具参数**。三档就是把我们的 CEO 填参面对齐到这一点；playbook 内部闸对应他们的「人写的流水线 / guardrail」。

**否决「砍更狠」**（把 `required_sections` / `citation_mode` 等内部验收也拆成纯提示词）：更狠会伤成篇两阶段引用时序——那些是开发者写死的流水线，不是 CEO 填参噪音。行业也不是「零验收」，而是验收不住在经理的参数表里。砍的边界停在**填参面**，不停在合同能力。结构不达标不因 `strict` 打 FAILED（与零写同档：COMPLETED + 提醒）。

**网页质检（已撤）**：不再对落盘 HTML/CSS/JS/SVG 做静态扫描或 HTML↔CSS 接缝。误伤做软件、属质量启发式；页面观感交给模型与浏览器壳、人审。合同字段 `web_quality_soft_exempt*` 一并删除。禁复活、禁再露 CEO schema。→ 见代码: `runtime/runs/contract.py`

**占位扫描（已撤）**：不再扫落盘 HTML/Markdown 的骨架电话、TODO、示例/虚构自注。质量交给模型、下一轮编辑与人看页。合同字段 `placeholder_hard_exempt*` 一并删除。禁复活、禁写进提示词。→ 见代码: `runtime/runs/contract.py`

### 交付契约：无死读兼容位

无运行时消费的键不保留：`must_contain_soft`（只解析、全仓无读点）；`completion.plan_suggests_exec_office_deliverable` / `append_sibling` 对已删 `deliverable.name` 的 `getattr`；占位扫描已撤后的 `placeholder_hard_exempt*`；`code_audit_gate`（已删审计结构闸，旧 JSON 未知键丢弃）。旧 JSON 多带未知键仍能加载（未知键丢弃）。→ 见代码: `runtime/runs/types.py` · `runtime/runs/builder.py` · `runtime/delegate/completion.py` · `runtime/coordination/append_sibling.py`

### 派单填参面

开局全队共识只走顶层 `team_brief`。≥2 篇完整成稿且共享口径未进 brief、无短规格 → 先 brief 或短规格岗，不靠边信道代替 `depends_on`（不新套餐、不复活 `consumer_deps` 猜两段）；审查线索进 brief；零上游成功补人走 `replaces_run_id`；根 CEO **默认非阻塞**（阻塞仅嵌套 lead / 成篇套餐提纲把关 / 画布人工把关）；用户明文看提纲 → `cite_write_review` 且 `checkpoint=true` 或先派再问；下游未定 → 先跑再 `replan(add)` / 再 `delegate`；轻/标准由引擎自判（单人 prose 可 auto-light）；上游保真有文件递路径、散文默认全文；扇入默认宽松（≥1 上游成功即跑，不让经理勾 AND/OR）。

**边界**：运行时仍解析多余键；playbook / 画布仍可写 `checkpoint_after`；`RunSpec.bind_after_deps` 槽位保留。测试仍可向 `execute` 传旧键。CEO 可见 schema → 见代码: `tools/builtin/delegate/schema.py`

### 不扫角色名改写 deliverable

不匹配 role 名正则/子串，也不据此**静默改写** deliverable（抬 `form=files` / 塞 `reviews/` artifacts / 追加纪律文案）。

审校落盘纪律**仅当** playbook 或 deliverable **已声明** `form=files` / 非空 `artifacts`（或等价结构 flag）时施加；`cite_write_review` 等在 playbook **写死**审校员 files 契约。成篇硬门只认具名 `playbook=cite_write_review`（无字数结构腿）。**不加**新 `completion_criteria` kind（见上节）。

删猜测入口优于保留误伤面；否决「降软但仍扫角色名」。能力回退用 playbook 结构补，不靠旁路正则。名叫「审校/review」但未声明 files 的轻角色不被抬契约。→ 见代码: `runtime/runs/research_quality.py`（结构谓词）+ `runtime/runs/playbooks/research.py`（审校 files 契约）

**委派一次性软提示族（已撤）**：不再扫 task 文案猜漏 `depends_on`（`consumer_deps`）、单 grant 设计+实现混装（`design_impl_same_grant`）、根单节点手写写工程无切片钉（`root_slice_honesty`）。三条都是成功路径上的一次性尾巴（不拒收、不改图），净负。`depends_on` 字段与 DAG 边仍在；假两段禁令已撤出提示词（编制交给模型），不默认真两段 / MVP 切片。禁复活扫描补闸。→ 见代码: `runtime/delegate/prelude.py`

### Per-worker 模型覆盖 {#per-worker-模型覆盖abc-同一功能}

每个 worker run 可绑与队友不同的大模型。省略 = 组合 Worker 槽（空则 follow 主）；CEO 在 `delegate`/`tasks[]` 可填节点显式。人侧 picker **不提供**人手改模。

**边界**：解析优先级 **节点显式 > 组合 Worker 槽 > follow 主模型**。wire 可保留 `model_overrides` 契约，**不**写产品确认面用法。续派 / 同人续跑：默认 **继承该 run 已解析模型**；本次 payload 显式改则覆盖。候选与组合槽、辩论身份对齐（统一目录 + BYOK 手填；platform allowlist）；非法配置 **硬失败**，禁 silent 回退。协作图与用量须能看见该队员用了哪个模型。CEO/`replan` `tasks[]` 目录身份（`@platform/…` / `@byok/…` 或提及）→路由键→`RunSpec.model`（`runtime/delegate/task_models.py`）；跨 provider 窄接 extras。sidecar inference proxy 认节点路由键。图 peek / 用量详情经 `run_completed.model`。凭据 / sidecar / 跨 origin → [平台 LLM 接入](/docs/05-平台与运维/平台LLM接入.md)。

能力开放（含 CEO 可选填）优先于「怕选不好就关能力」；确认面藏人改模 UI，**不**删后端节点显式 / 契约字段。**否决**角色名猜模、质量档矩阵、账号级角色→模型主设置、无可见性的暗箱路由。旧 `ModelTier` / `model_preference` 档位体系仍废，不复活。

## 冷启动探索幕

**触发**走软硬分层——**触发条件表不在本文**：闸看的全是记忆态（画像空否 / `explore_workspace_key` / 指纹），权威 → [记忆 · 探索触发与挡请求](/docs/03-AI核心/Agent记忆与知识系统.md)。本文只管**开幕之后怎么编排**。

一句话记住分层：软幕（仅空画像）不挡请求；硬挡三因（换绑 / 用户点名 / 空画像+工程信号）先探索再继续；指纹漂移不挡，走脏标记 + 旁路刷新。

**硬挡流程**：注入 `<cold_start_explore>`（换绑 / 点名刷新 / 空画像+工程信号；指纹与「仅空画像」**不**进此块）→ 先轻探定位入口（禁止自己摸完整仓；停手见编排 skill【工作流】，不在场面门复述轮次配额）→ `delegate` 调研建档（按活的结构组队，场面门不写人数硬账；同其它委派直接开跑）→ 收尾经 `update_folder_profile` 合并写文件夹 **画像 + 导航.md**，记录 `workspace_key` 与指纹；主题软顶 5 / 总数受 `memory_max_topic_files` → **立刻继续原请求**。pending 期间允许 `form=files`，但写盘不得出 `explore_memory` 根。**结构化例外**：本回合 `create_folder`（及裸聊自动建桌的首次铸造）得到的 folder id，点名该 id 的 worker 用 `write_scope=project`——空新桌没有要保护的已有工程，填文件即任务；当前会话出生文件夹仍走 `explore_memory`。厚背景资料（`主题/` 条目）不在本幕写。产物谁写 → [记忆 · 产物谁写](/docs/03-AI核心/Agent记忆与知识系统.md)。点名硬闸与 pending 同级。**resume 与开场同源**：空画像软降级走 `resolve_hard_explore_reason`，禁止 resume 把「仅空画像」误硬拦。

**强制 / 豁免**：点名强制开幕（合并更新）。旧画像无 key → 不因缺 key 硬开。裸聊 / 纯闲聊 / 空工作区不自动开幕、不写假画像/导航。对已有工程「继续开发 / 全面摸底」按活的结构组队（禁止因话题像架构 / 盘点就套固定人数）；冷启动建档仍宜 ≥2 角（产品宜；场面门与编排 skill 都不写人数硬账/除外，组队不靠引擎逼）。引擎**不**按探路轮数剥调查工具、**不** `content_reset` 丢稿逼 delegate；组队靠提示词（CEO 自判、探路≠摸底、按活的结构组队；不因话题像架构而开组，缺证先取证、取证≠必须开组）。冷启动 pending 时亦不因 delegate 节点数 <2 硬拒。成篇形状 / 修码选型 / 跑·修·打开验证终向 / 点名对比扇出靠提示词与结构验收，不靠意图分类器。成篇审计硬门只认成文专线 `playbook=cite_write_review`（无字数结构腿）；`map_fanout` / 普通多角摸底不进硬门（软闸亦同）；硬门**不**扫 task/角色自由文。审校落盘靠 playbook/结构声明，不扫角色名。审后默认向用户收口，同轮 `continue_from_run_id` 修订非默认路径。

**边界**：不新建 Explore 原语；指纹 = 顶层树 + 关键清单（不以纯天数 / commit 为唯一闸）。产物只落 `AgentCore/`（记忆；厚背景资料走 `主题/` 按需条目，且不在探索 pending 批）。**权威分层**：触发条件 / 产物谁写 / 主题上限 → [记忆 · 探索触发](/docs/03-AI核心/Agent记忆与知识系统.md)；主题不由闲聊巩固写 → [记忆 · 三、维护协议（情景沉淀 → 语义巩固）](/docs/03-AI核心/Agent记忆与知识系统.md)；`delegate` 组队纪律 / 收尾 → **本文**。

**否决（本定案）**：pending 时按 `form=files` 拒整批；pending 时按 delegate 节点数硬拒单 worker（现状组队靠提示词，人数硬拒已拆）；为冷启动单开 prose 版调研 playbook；delegate 入口静默改写 playbook/tasks XOR。

## 失败与否决（一行）

| 场景 / 方案 | 处理或否决理由 |
|---|---|
| `delegate` 参数非法 | 非终态回 CEO，改参重试 |
| 单 worker 失败 | 按 `on_failure`；宽松扇入默认放行，不必拖垮整 DAG |
| 无需团队 | 不调 `delegate` = 单 Agent 直答 |
| 纯路由器替换 CEO / 前置分类器 / Worker 直连 / 取消 CEO 综述 | 规划壁垒、编排税、不可观测、丧失「一个声音」 |
| 累计 N 次只读软提醒护栏 | A/B 净负已移除；靠提示词边界 + 失控硬兜底 |
| 累计失败收走 `run` / 打开网页 | ✅ **已撤**（作业失败 ≠ 能力没了）。`run` 亦不因环境死 / 干等 / 探测失败卸工具；打开网页只认出网硬退役 → [执行引擎 · 收敛治理](/docs/03-AI核心/执行引擎架构设计.md) |
| 空交 / 零声明清单整轮失败 | ✅ **已撤**（实测误伤；未声明 `file_write` 仍算有产出；空交接风暴 / 空 handoff 硬拒已拆） |
| 合同结构不达标（缺章节）打 FAILED | ✅ **已撤**（与零写同档：返工后仍 COMPLETED + 提醒；`strict` 不翻成败） |
| 整合员必须 `file_write` 硬闸 | **否决**（派单点名上游路径 + 同一人 `continue_from`；不拦口头综述） |
| 默认加硬闸/软闸「优化服从度」 | **否决**；阶梯见 `.cursor/rules/intercept-discipline.mdc`（提示词→观测→一次性软提示→结构化硬拒） |
| 跨回合把对账缺口抄进下一轮提示 / 短确认必须只补洞 | ✅ **已撤**（下一轮要什么交给船长听老板；误伤看效果。`delivery_status` 仍供合回与同轮档位） |
| 扩大收口姿势 A / 完成话术正则冒充近零误报 | **否决** → [执行引擎 · 可用性诚实性](/docs/03-AI核心/执行引擎架构设计.md) |
| 未派工靠禁语表 / 完成话术正则拦正文 | ✅ **已撤**（诚实性走 `team_batch` 结构面）→ 同上 |
| 无对账卡时拦同条 A∪C | ✅ **已撤**（无卡不拦正文）→ 同上 |
| 零写落盘声称扫词硬回炉（答完清气泡） | ✅ **已撤**（解释禁语误伤）→ 同上 |
| 产物结构窄闸（请下载 / 无 pptx 说 PPT / 点名缺席路径） | ✅ **已撤**（与零写同构；误伤比漏拦贵）；禁复活、禁提示词补闸、禁改影子当真拦 → 同上 |
| 浏览器声称扫词硬回炉（无成功却说已开页 / 已登录） | ✅ **已撤**（与零写 / 产物结构同构）；检测器可留；禁复活、禁提示词补闸 → 同上 |
| handoff `motion_card`（schema / 硬拒 / CEO 展开块） | ✅ **已撤**（开辩由用户点名，不靠交接卡催场）；`parse_motion_card` / 历史 debrief JSON / `汇总与命题卡.md` 留下；禁提示词补闸 |
| 空语言围栏回炉（标了语言却空体） | ✅ **已撤**（质检启发式）；未闭合围栏仍回炉 → [执行引擎 · finish_guard](/docs/03-AI核心/执行引擎架构设计.md) |
| B1 空心措辞扫描（清气泡 / 观测检测器） | ✅ **已删**；超席 / 超时 / 掐断 latch 留下；禁提示词补闸 → 同上 |
| 领域 kind 扩表 / 边删 kind 边加启发式完成硬闸 | **否决**；接盘见上节 |
| `validate_criteria_kind_fit` 扫 task 拟合硬闸 | **已撤** |
| `host(action=shell)` fuse 改可批可跑（B）/ 仅改文案当终案（D） | **否决** → [安全 · 熔断方案 C](/docs/05-平台与运维/安全权限与治理.md) |
| 扫角色名静默改写 deliverable | **已删**（见上节） |
| 扫 role·task 自由文正则决定产物落 `research` 还是 `reviews` | **净删除**：意图分类器形态，且误判对用户不可见；落点只认显式来源；裸文件名进 `工作稿/`，空 `artifacts` 不钉目录 → [工作区 §四](/docs/02-架构/双模式工作区.md#四约定文档目录约定) |
| 产物地位（成品 vs 过程材料）靠路径推断 / 派单预判 / worker 自判 | **否决**（worker 无自贬动机）；用户点名拿走才钉路径 / `form=workspace`；经理不发明路径，工人自起名是默认；裸文件名进 `工作稿/`，空 `artifacts` 不钉目录，打开走终稿路径 / 工作区树。收口再搬的 `promote_product` **已撤销** → [术语表 · 成品归位](/docs/01-产品/术语表.md) |
| 约定目录未命中软提醒催 CEO 归位 / 返工 | ✅ **已撤**（有落盘即认盘；`artifact_dir` 不命中不发软待办）。点名 `artifacts` 未命中仍可 warning。禁复活催搬、禁提示词补闸 → [执行引擎 · 路径验收](/docs/03-AI核心/执行引擎架构设计.md) |
| 手写 `min_length` 字数腿 / 扫自由文补成篇硬门 | **否决**（成篇硬门只认 `cite_write_review`；见 CEO 填参面） |
| 裸 `requires_files` 第三写盘开关 | **已删**（写盘只认 `form=files` ∪ `form=workspace` ∪ `artifacts`；漏填=`files`） |
| `must_contain_soft` 空兼容位 | **已删**（见死字段清理） |
| 连 playbook 内部验收字段一并拆成纯提示词 | **否决**（砍更狠；见派活单三档） |
| 按场景加 playbook（建站 / 做软件 / 对比选型 / 单功能交付） | **否决**；已删 `build_app` / `compare_options` / `build_feature`（及更早建站本）；做软件 / 点名对比 / 局部功能改手写；做软件无专段 HOW |
| 编制「能少则少 / 真并行才多人」当默认 | **否决**（人数不是优化目标；1 人只在活本身是一块；勿按工种头衔凑满） |
| 做软件禁单 HTML 薄旁路 | **否决**（硬闸已撤，禁提示词补闸） |
| 讨论类开场三选卡当产品菜单 | **否决**（闲聊自己回；干活默认派；猜错会做错才短问。拍板 HOW 在 `asking_the_user` / 检查点） |
| 绿场默认真两段 / 「立刻派 ≠ 立刻全量」缩收 | **否决**（档没钉且猜错会做错才短问；假两段禁令已撤出提示词） |
| 交付类 ask 建议档闭集（主路径 / 一次做完 / 只改一处） | **否决**（挡路问做到哪一档；label 写桌上结果、不映射编制；闭集被口头译成 MVP） |
| 成文编号树（默认 A / 档 1–3 / C·D·E）当闭集分类器 | **否决**（有结构就派、该落盘就落盘；满编审校仅长文 / 可提交 / 用户点名） |
| 用桌上结果当 playbook 名（调研 / 审计 / 修码） | **否决**（准入第 5 条；名形状；不设旧 id 别名） |
| 把 brief / 对比 / 多透镜合成一本再加 `kind=` | **否决**（分叉藏进一名，比分本更难路由） |
| 网页质检（anti-slop / 假电话 / 未闭合标签 / HTML↔CSS 接缝） | ✅ **已撤**（误伤做软件、质量启发式；观感交给模型与浏览器壳）；禁复活、禁再露 CEO schema |
| 占位扫描（骨架电话 / TODO / 示例·虚构自注） | ✅ **已撤**（质量启发式；文案交给模型）；`placeholder_hard_exempt*` 一并删除；禁复活、禁写进提示词 |
| `consumer_deps` 扫 task 漏边软提示 | ✅ **已撤**（净负软尾巴）；禁复活、禁提示词补闸 |
| 两篇成稿无依赖并肩靠复活 `consumer_deps` / 扫长文猜该不该两段 | **否决**；skill「两篇成稿·先口径」；对齐走 `team_brief` / DAG → [协作模式](/docs/03-AI核心/Agent协作模式.md) |
| `design_impl_same_grant` 设计+实现同 grant 软提示 | ✅ **已撤**（净负软尾巴）；禁硬拒 / 自动改图 / 复活 |
| `root_slice_honesty` 根单节点手写写工程无切片钉 | ✅ **已撤**（净负软尾巴）；路径 B 嵌套仍合法；禁硬拒 / 扫长文 / 用 `write_scope` / 复活 |
| 载体/手段纠偏靠硬闸 / 意图分类器 / 复活 `format_options` | **否决**；盖不住才短问，次优标假设继续（见上节 · 载体/手段） |
| 次优手段一律停下来发卡 | **否决**（打断心流；可逆次优标假设继续） |
| 把「讨论」编码成免探索场面（讨论不必查 / 讨论读文档不是摸底） | **否决**；缺工作区证据须先取证，取证≠必须开组；「讨论」不是路由键 |
| 账号级角色→模型矩阵 / `ModelTier{fast,strong}` 质量档 / 自动降级 / silent 回退野模型 | **否决**（与 per-run 显式覆盖正交；见上节 Per-worker） |
| 无 UI 的 CEO 暗箱选模（有字段但图/用量不可见） | **否决** |

## 检查点

`ask_user` 通用澄清、成篇套餐 / 画布人工把关的波间停顿 → 全文见 [检查点](/docs/03-AI核心/检查点与开工卡.md)，本文不复述。
