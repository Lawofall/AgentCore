// SSE event contract — shared source for desktop + mobile folds (前端技术与架构 §十 SSE 与协议一致性).
// Event names: backend EventType → eventTypes.generated.ts
// Payload shapes (incl. SSEEvent): backend payloads/*.py → events.generated.ts (`pnpm gen:types`)
//
// Do not redeclare SSEEvent here — that shadows the generated wire shape.

export * from "./events.generated";
