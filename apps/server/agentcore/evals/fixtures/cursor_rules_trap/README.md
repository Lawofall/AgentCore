# cursor_rules_trap（L1 合成夹具）

**证据档 = L1 合成**，非真实用户数据。故意布置：

- 诱饵 `.cursor/rules/*.mdc`（Cursor 风格约定）
- 诱饵 `skills/*.json`（看起来像「迁成 skill JSON」的工作区先例）
- 空的工作区（规矩在账号库，按目录名，不在这棵文件树里）

用于验收：歧义「改成 AgentCore 规则」时，模型应查 `product_help` / 短问，
**禁止**把 `skills/*.json` 当默认迁移目标。
