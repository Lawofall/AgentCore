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
| 用户规则内容、落盘、按需目录 | `agentcore/memory/`（包 facade 已 re-export）+ `file_ops` overlay | — |
| 注入段序 / `ContextAssembler` / 工作区概览 | `runtime/context/` + 回合拼装 `runtime/resolve/`（含 `prompt/memory_rules.py`） | 勿在 assembler 写规则策略或落盘 |
| Run / graph 执行 | `runtime/runs/` → [执行引擎](/docs/03-AI核心/执行引擎架构设计.md) | **不是**用户规则域 |

---

## 现状

| 通道 | 现状 | 因 |
|---|---|---|
| **用户规则** | 进 `<按需目录>` / `consult` | 人保存的摘要才入目录；正文在这篇 md |
| **过往事实** | `search_conversations` / `read_conversation` | 旧场原文可查，不靠系统总结 |
| **AI 笔记** | **停写停注入** | 系统不擅自总结；存量留盘当普通稿、不进下一场 |

产品面没有「AI 记忆」活能力：不写画像 / 导航 / 主题，不开探索幕，不情景沉淀。

---

## 用户规则 {#用户规则}

进模型的是人保存进索引的 Markdown。没有类型字段。这篇是规矩、记忆还是别的用途，由正文说明。文件页已取消「记忆 / 规则 / 文档」三夹。

**入目录**：索引上的 `description` 非空才进 `<按需目录>`。这列只在人保存时从正文 frontmatter 派生。模型写入可以改正文，不改这列，新行因此不进目录。空摘要不进目录，也不占名字挡住别的桌。不回退取正文首行。→ 见代码: `memory/rule_resolve.py`

**看见范围**：账号（`folder_id` 空）和当前桌由外向里。同名只留最近一张已入目录的桌。删文件夹带走这张桌的篇；账号上的不陪葬。

**注入**：只有按需。目录行是名字加这句摘要，`consult` 取正文并剥掉 frontmatter。人在这一场 `@` 某篇，正文直接带上（`<钉住条目>`）。不进 `<设定>`，不进 `<路径约定>`。

**正文**：这篇 md 是正文。frontmatter 解析失败不注入。不引 YAML。已知键里参与入目录的是 `description`。

```
---
description: 一行摘要
---
```

`apply` / `paths` 不参与注入。`ai_maintained` 不进 frontmatter。

**谁写**：人在工具箱或文件页保存这篇 Markdown，摘要写入索引。模型改的是同一篇文档的正文。产品能力目录、工作区路径不进这些文档 → [现行信息](/docs/01-产品/现行信息.md)。

**纠错 UI 已撤**。手写提示词用删除。存量 `disputed_at` 见代码，不当人侧入口。**严禁**扫对话原文猜「用户是否在否认某条规则」。

### 注入 {#注入}

账号层条目进不进这场，由 [装配](/docs/03-AI核心/工具与能力系统.md#装配) 的成员名单决定。回合武装了装配之后只注入这份名单里已入目录的篇。文件夹层由外向里叠，同名只留最近一张桌。

1. 本场对话历史经 `load_recent_history` 进窗口（CEO / worker 共用）。
2. 用户规则不进 `<设定>`，不进 `<路径约定>`。
3. 桌面 sidecar **有 account 票**时：prepare/resume 对按需目录**只读进程快照**（miss → 空注入、不 await 云 HTTP）；`consult` 取正文与目录同一份快照。快照 **300s TTL**；人保存后立刻强制重暖。**detached execution 存活期**按同一 TTL 周期续暖——只 warm 一次则 TTL 到期后目录会**静默**全失。空注入仍打 `account.rules_memory_cache_miss`。**无票**仍走本地 DB。→ 见代码：`memory/rules_injection.py` · `memory/account_prepare_cache.py` · sidecar `warmAccountRulesMemory`
4. 用户规则与系统 Skill、低频工具合并为单一 `<按需目录>` → `consult`。`<按需目录>` 非空才渲染。
5. **跨文件夹名册**（派生）：**不进** CEO 常驻提示。当前桌只在 `<工作区>`；其它桌用 `folders`。
6. 装配顺序权威 → [执行引擎 §七](/docs/03-AI核心/执行引擎架构设计.md) / `runtime/context/`（`SectionOrder`）。

**删文件夹带走这张桌的设定（✅）**：软删桌子 → 该 `folder_id`（及子树）的设定退出注入，行仍留着；恢复桌子时一起回来。账号级条目不陪葬。→ 见代码: `memory/scope_chain.py` · `folders/permanent_delete.py`

约定根 UI = **`.agentcore`**（磁盘仍是 `AgentCore/`）。人保存的条目挂在这里；存量 `记忆/` · `文档/` 若在盘上当普通子项，不进目录。用户要拿走的文件在派单时写入工作区 → [工作区 §四](/docs/02-架构/工作区.md#四约定文档目录约定)。

---

## 过往事实 {#过往事实}

Worker / CEO 默认开场持 `search_conversations` / `read_conversation`。**过往事实走这里**，不靠笔记。工具箱「对话」关掉则这两把离开开场表和执行。用户 `@` 一场旧对话仍由服务端塞对话稿，不靠这两把。工具面（query / folder_id / focus / 分页）权威 → [工具与能力 · 跨会话工具边界](/docs/03-AI核心/工具与能力系统.md#四跨会话工具边界)。

对外口径：白话三层——当前会话 / 你写的规矩 / 旧场可查；不报工具名。→ 见代码：`core/search_query.py` · `conversation/log_export.py` · `tools/builtin/search_conversations.py`

---

## 否决 {#否决}

| 方案 | 理由 |
|---|---|
| AI 笔记当活产品（写 / 注入画像、导航、主题） | **停写停注入**；系统不擅自总结。存量留盘当普通稿 |
| 闲聊巩固 / 情景沉淀进 prompt / 探索幕写画像 | 一场任务渣进每回合；了解这张桌当场读文件，过往查旧对话 |
| 独立 `user_memory` 表 / 记忆总闸 / 跨用户记忆包 | 与文件树重叠或难懂；编辑/清空规则与删对话已够 |
| 用户规则常驻 / 碰到文件 | 用户规则只有按需。摘要空着就不进目录 |
| 用户规则 `conditional` | 没有可证明的触发 |
| 模型写入自动入目录 | 摘要谁保存记在索引上。正文里写一句摘要不够 |
| frontmatter 与 DB 列双写；解析失败猜默认值 | 两个可写副本必然分叉；失败须可见 |
| `ai_maintained` 进 frontmatter | AI 能伪装成用户规则、绕开写侧闸 |
| 扫自由文猜「改规则 / 否认某条」 | `intercept-discipline` 点名否决的意图分类器 |
| 把产品能力目录写入用户规则 | 真源在产品；抄进规则会过时且挤按需目录 |
| 向量 chunk 自动灌 prompt | 与「文件随时变」不合；agentic 自取永远新鲜 |

查看/编辑：工具箱提示词，以及文件夹文件页里的同一篇 Markdown。→ 见代码：`fileWorkbench/EntriesSection.tsx`
