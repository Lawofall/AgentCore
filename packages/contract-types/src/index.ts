// @agentcore/contract-types — 桌面/手机两端共享的契约类型单一源（前端技术与架构 §十 SSE 与协议一致性）.
//
// SSE 事件名：后端 EventType → eventTypes.generated.ts（`pnpm gen:types`）。
// SSE payload：后端 payloads/*.py → events.generated.ts。REST DTO：见 @agentcore/contract-rest-types。
// InteractionKind / ErrorCode：后端枚举 → interactionKinds.generated.ts / errorCodes.generated.ts。
// 恋综：`sim.show.*` 进 EventType 生成管线；showEvents 为别名索引；episodeManifest 仍为手写 schema。
export * from "./errorCodes";
export * from "./interactionKinds.generated";
export * from "./events";
export * from "./showEvents";
export * from "./episodeManifest";
