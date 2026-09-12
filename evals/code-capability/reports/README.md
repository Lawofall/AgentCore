# R 真仓 reports（LLM 烟感 / 时间戳副本不入仓；棘轮基线应入库）

- `r1_baseline_latest.json` / `r1b_baseline_latest.json` — `r1_control.py --suite all --mode matrix`（Find/Fix；含 R1a+R1b；10 仓 by_vendor）
- `r1a_baseline_latest.json` — `r1_control.py --suite r1a` 或 `r1a_control.py --mode matrix`
- `r2_baseline_latest.json` — `r2_control.py --mode matrix`（**Extend** 分册；V01·V04·V09·V10）
- `r3_baseline_latest.json` — `r3_control.py --mode matrix`（**Collab** 分册；V01·V07；含 `collab_diagnostics`）
- `*_baseline_<UTC>.json` — 带时间戳副本（**不入仓**；权威是 `*_baseline_latest.json`）
- **`baselines/`** — R4 冻结棘轮副本（`r1.json`·`r2.json`·`r3.json` + `manifest.json`）；对比见 [`../r4_regress.py`](../r4_regress.py)；bump 纪律见 [`baselines/README.md`](baselines/README.md)
- **`llm_smoke_latest.json`** / `llm_smoke_<UTC>.json` — D·sidecar LLM 真跑烟感（[`../r_llm_smoke.py`](../r_llm_smoke.py)）；**不入仓**，本地跑出。与无 LLM 对照矩阵分列

报告字段：分仓 · 卡 · mode(fixed/broken) · pass/fail · fail_class（control/harness）。Find/Fix · Extend · Collab **分列**，勿混口径。R3 另含 `collab_diagnostics`（`run_plan` / `worker_files` 软字段，不进 hard_accept）。LLM 烟感另见 `llm_smoke_*.json`（verdict / conversation_id / trace_id / fail_class）。