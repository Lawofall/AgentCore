"""Protocol conformance — the backend-authoritative ProjectedTurn oracle + golden export.

The single source for the cross-platform protocol巡检 (前端技术与架构 §十 SSE 与协议一致性):
the backend projects each event vector into the normalized :data:`ProjectedTurn` (the
neutral judge),
and :mod:`export` writes the (vector, golden) pairs to ``packages/protocol-conformance/
fixtures`` so each frontend's ``fold`` can be asserted ``== golden`` via ``pnpm conformance``.

Why here and not just in tests: the doc makes the BACKEND the漂移震中 — one place owns the
event types, the vectors, and the golden — so "两端不一致时谁对" has an answer (the oracle).
"""
