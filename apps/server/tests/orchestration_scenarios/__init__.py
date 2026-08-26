"""整链入口：真实 ``run_chat_pipeline`` / ``resume_chat_pipeline``，LLM 全脚本化、零真网。

再加场景：在 ``RoleScriptedProvider`` 加 mode（复用 ``tests.delegate.conftest`` 与 ``tests.llm_helpers``），
经 ``run_orchestration_turn`` 跑，只断言 journal kinds / SSE 类型序 / finish，不锁 CEO 提示词。
挂起后再续：pause 用 ``run_orchestration_turn``，冷 round-trip 用 ``cold_claim_frame``，
再走 ``run_orchestration_resume``。
"""
