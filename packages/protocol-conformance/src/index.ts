// @agentcore/protocol-conformance — 协议一致性巡检规范（前端技术与架构 §十 SSE 与协议一致性）。
//
// 承载：ProjectedTurn schema（裁判态）+ harness（向量加载 / 深度 diff / runner）。
// golden 向量是后端导出的 JSON（fixtures/*.json，单一源 = runtime/conformance），本包
// 不含任何 app 业务实现——每端用自己的 fold 调 runConformance()。
export * from "./projectedTurn";
export * from "./failureFace";
export * from "./turnVerdict";
export * from "./turnVerdictGaps";
export * from "./fixtureKind";
export * from "./harness";
