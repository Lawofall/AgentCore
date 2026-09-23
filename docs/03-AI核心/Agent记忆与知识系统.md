---
status: landed
code: apps/server/agentcore/memory/
related:
  - docs/03-AI核心/上下文传递可视化.md
  - docs/03-AI核心/上下文工程.md
  - docs/02-架构/工作区.md
  - docs/03-AI核心/工具与能力系统.md
  - docs/01-产品/现行信息.md
  - docs/03-AI核心/编排器与CEO主Agent.md
skip_if:
  - 只改 World A/B 提示词架构或 World B 内部工具提示词（读执行引擎 §七）
---

# Agent 记忆与知识系统

> **边界**：用户规则 How = **本文**；通道可视化 → [上下文传递可视化](/docs/03-AI核心/上下文传递可视化.md)；注入侧 Assembler / 按需与写侧配额 → [上下文工程](/docs/03-AI核心/上下文工程.md)；云/本地 Backend → [工作区](/docs/02-架构/工作区.md)；旧对话检索工具面 → [工具与能力 · 跨会话工具边界](/docs/03-AI核心/工具与能力系统.md#四跨会话工具边界)。
>
> → 见代码：`apps/server/agentcore/memory/`
>
> **主循环归属**：用户规则归「**组**」；旧对话检索归「**说**」。不当第六拍 → [主循环](/docs/01-产品/产品定位与品牌.md)。

### Cursor 从哪进

| 要改… | 去哪 | 勿当入口 |
|---|---|---|
| 用户规则内容、落盘、常驻配额 | `agentcore/memory/`（包 facade 已 re-export）+ `file_ops` overlay | — |
| 注入段序 / `ContextAssembler` / 工作区概览 | `runtime/context/` + 回合拼装 `runtime/resolve/`（含 `prompt/memory_rules.py`） | 勿在 assembler 写规则策略或落盘 |
| Run / delegate 执行 | `runtime/runs/` → [执行引擎](/docs/03-AI核心/执行引擎架构设计.md) | **不是**用户规则域 |

---

## 现状

| 通道 | 现状 | 因 |
|---|---|---|
| **用户规则** | 进 `<设定>` / `consult` | 规矩你写；编制进下一场 |
| **过往事实** | `search_conversations` / `read_conversation` | 旧场原文可查，不靠系统总结 |
| **AI 笔记** | **停写停注入** | 系统不擅自总结；存量留盘当普通稿、不进下一场 |

产品面没有「AI 记忆」活能力：不写画像 / 导航 / 主题，不开探索幕，不情景沉淀。

---

## 用户规则 {#用户规则}

基座里进模型的是**用户自己的规则**：带 frontmatter 的 md 条目。文件页已取消「记忆 / 规则 / 文档」三夹。

**条目形态**：正文 md + frontmatter 已知键——生效（`apply: always | on_demand | paths`，UI「常驻 / 按需 / 碰到文件」）、`description`（一行摘要，空时异步生成、非空永不自动覆盖）、`paths`（仅 `apply: paths` 时必填，逗号分隔的工作区相对 glob）、可选 `offers_tools`（存量 / 市场快照列出用到的工具名，逗号分隔；编辑面不再写，不挡开场表）。没有类型、没有权威档、没有来源标记。作用域是挂载关系（全局 vs 某文件夹），不是字段。

**frontmatter 是唯一可写真源**，DB 列（`apply_mode` 等）是派生索引。不一致时 md 无条件赢。写入须过仓储层唯一派生点 `_set_content_and_derive`。**frontmatter 解析失败 = 不注入 + UI 明确报错**。注入时剥掉 frontmatter 再喂模型。→ 见代码: `documents/frontmatter.py`

```
---
apply: on_demand        # always | on_demand | paths；缺省 on_demand
description: 一行摘要    # 可空；空不是错误。按需与路径都靠这一句
paths: "**/*.tsx"        # 仅 apply: paths；* 不跨 /，** 跨
offers_tools: host, debate  # 可选
---
```

- **已知键只有这些**。名字由文件名承载、作用域由目录层级承载。生效档、摘要进 frontmatter。`offers_tools` 是可选存量键（给人看），不是启用闸。
- **生效三档**：`always | on_demand | paths`。**否决** `conditional`（没有可证明的触发）。常驻把正文打进 `<设定>`。按需进 `<按需目录>`，`consult` 只取正文。路径不进目录：每回合一条 `<路径约定>`（模式 + 这一句）；全文只在已经出现的路径旁边（附件、`@` 文件、`read` / `write` / `edit` 的 `file_path`）。不从用户自由文猜路径。`*` / `**` / `**/*` / `**/**` 算常驻，占常驻配额。
- **`ai_maintained` 不进 frontmatter**：写入者身份，真源留 DB。读侧只注入 `ai_maintained=false` 的用户规则。
- **不引 YAML**：已知键按 `key: value` 行解析；未知键当不透明文本保留；写回走文本级最小编辑。
- **键缺席 ≠ 解析失败**：没写 `apply` 缺省 `on_demand`。`apply` 写了但读不懂 → 报错，不退回缺省。`apply: paths` 没有 `paths` 模式 → 报错。

**`description` 是枢纽**：按需目录只认名字 + `description`，**不**回退取正文首行。空摘要 = 实际不可检索。→ 见代码: `documents/description.py` · `memory/rules_injection.py`

**注入**：

- **常驻** → 只叠用户自己的常驻规则（全局 → 祖先外→内 → 当前），含无界路径规则。同名只留最近一张桌的那一份。✅
- **路径** → `<路径约定>` 在稳定前缀里，紧挨 `<设定>`。全文在信封或该次工具结果上，不改写 `role: system` 的前半。空 `description` 不进索引。
- **按需** → **一个**目录（名字 + `description`）+ **一个** `consult`（skill / 用户规则；低频工具另挂）。空 `description` 占住名字、不进目录。拉不到为软 miss。
- **`@` 提及** = 一条按需条目临时当常驻；对话页 `@` 点名设定走 `kind=document`，注入 `<钉住条目>`。→ 见代码: `runtime/resolve/attachment_context.py`

**谁写**：对话里 `write` `.agentcore/rules/*.md`（一个主题一篇，整篇覆盖；本批能否写跟 `write_scope`，与写工作区文件同一道）与工具箱提示词 / 文件页。半截/`…` 收尾拒写入。给人看先写在对话里，确认后再写。产品能力目录、工作区路径不进用户规则 → [现行信息](/docs/01-产品/现行信息.md)。

**纠错 UI 已撤**。手写提示词用删除。存量 `disputed_at` 见代码，不当人侧入口。**严禁**扫对话原文猜「用户是否在否认某条规则」。

### 注入 {#注入}

1. 本场对话历史经 `load_recent_history` 进窗口（CEO / worker 共用）。
2. `<设定>` 只叠用户常驻规则：全局 → 祖先外→内 → 当前。同名硬覆盖，标签只说在哪张桌。`<路径约定>` 跟在后面，同样每回合稳定。改这两块会打穿前缀缓存；DeepSeek 窗内替换认这两块和 `<按需目录>`。
3. 桌面 sidecar **有 account 票**时：prepare/resume 对 always / on_demand / paths **只读进程快照**（miss → 空注入、不 await 云 HTTP）；`consult` 取正文与目录同一份快照。快照 **300s TTL**；本机文件页写入与 sidecar 上写 `.agentcore/rules/` 成功后立刻强制重暖。**detached execution 存活期**按同一 TTL 周期续暖——只 warm 一次则 TTL 到期后用户规则会**静默**全失。空注入仍打 `account.rules_memory_cache_miss`。**无票**仍走本地 DB。→ 见代码：`memory/rules_injection.py` · `memory/account_prepare_cache.py` · sidecar `warmAccountRulesMemory`
4. on_demand 侧（用户规则 / 系统 Skill / 低频工具）合并为单一 `<按需目录>` → `consult`。同名跨常驻 / 按需 / 路径只留最近一张桌。`<按需目录>` 非空才渲染。路径全文不进这个目录。
5. **跨文件夹名册**（派生）：**不进** CEO 常驻提示。当前桌只在 `<工作区>`；其它桌用 `folders`。
6. 装配顺序权威 → [执行引擎 §七](/docs/03-AI核心/执行引擎架构设计.md) / `runtime/context/`（`SectionOrder`）。

**配额：闸在写侧，读侧全量。** 常驻满了不许再往常驻加；无界路径规则占同一池。读侧永远全量注入、不截断。唯一界是 `memory_always_max_chars`。闸对人不可见，只拦 AI 写入用户规则；用户编辑已有常驻致超限 → 放行 + 警告。AI 写入遇满 → 停摆推卡，不降级、不淘汰。超窗须失败说清。→ 见代码: `memory/always_quota.py`

**删文件夹带走这张桌的设定（✅）**：软删桌子 → 该 `folder_id`（及子树）的设定退出注入，行仍留着；恢复桌子时一起回来。账号级条目不陪葬。→ 见代码: `memory/scope_chain.py` · `folders/permanent_delete.py`

约定根 UI = **`.agentcore`**（磁盘仍是 `AgentCore/`）。活柜是用户规则条目；`rules/` 为对话内写入路径。存量 `记忆/` · `文档/` 若在盘上当普通子项，**不进** `<设定>` / `consult`。用户要拿走的文件在派单时写入工作区 → [工作区 §四](/docs/02-架构/工作区.md#四约定文档目录约定)。

---

## 过往事实 {#过往事实}

Worker / CEO 开场即持 `search_conversations` / `read_conversation`。**过往事实走这里**，不靠笔记。能力产品层恒开。工具面（query / folder_id / focus / 分页）权威 → [工具与能力 · 跨会话工具边界](/docs/03-AI核心/工具与能力系统.md#四跨会话工具边界)。

对外口径：白话三层——当前会话 / 你写的规矩 / 旧场可查；不报工具名。→ 见代码：`core/search_query.py` · `conversation/log_export.py` · `tools/builtin/search_conversations.py`

---

## 否决 {#否决}

| 方案 | 理由 |
|---|---|
| AI 笔记当活产品（写 / 注入画像、导航、主题） | **停写停注入**；系统不擅自总结。存量留盘当普通稿 |
| 闲聊巩固 / 情景沉淀进 prompt / 探索幕写画像 | 一场任务渣进每回合；了解这张桌当场读文件，过往查旧对话 |
| 独立 `user_memory` 表 / 记忆总闸 / 跨用户记忆包 | 与文件树重叠或难懂；编辑/清空规则与删对话已够 |
| 用户规则 `conditional` | 没有可证明的触发。路径档只认 glob，不扫自由文 |
| 常驻分池 / 自动淘汰 / 读侧截断 / 文件页用量条 / 条目行尾字数 | 引擎不替用户挤；用量条会把安全阀读成「还能再塞」。混排列表上的字数空白会被读成缺数据；精确字数只留工具箱「必带」 |
| frontmatter 与 DB 列双写；解析失败猜默认值 | 两个可写副本必然分叉；失败须可见 |
| `ai_maintained` 进 frontmatter | AI 能伪装成用户规则、绕开写侧闸 |
| 扫自由文猜「改规则 / 否认某条」 | `intercept-discipline` 点名否决的意图分类器 |
| 把产品能力目录写入用户规则 | 真源在产品；抄进规则会过时且挤按需目录 |
| 向量 chunk 自动灌 prompt | 与「文件随时变」不合；agentic 自取永远新鲜 |

查看/编辑：对话内 `write` `.agentcore/rules/*.md` 与工具箱提示词 + 各文件夹 `.agentcore`。→ 见代码：`fileWorkbench/AgentCoreSection.tsx` · `EntriesSection.tsx`
