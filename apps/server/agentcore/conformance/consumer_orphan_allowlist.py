"""Allowlists for the consumer-orphan gate.

Keys: ``sse:<wire>`` | ``interaction:<kind>`` | ``process_step:<kind>``.
Each value must state why the type is exempt. Default empty — no silent exemptions.

``CONSUMER_ORPHAN_ALLOWLIST``: still in the generated contract, no live Python producer.
``PRODUCER_ORPHAN_ALLOWLIST``: Python producer, no desktop live UI consumer.
"""

from __future__ import annotations

CONSUMER_ORPHAN_ALLOWLIST: dict[str, str] = {
    "process_step:team_preview": (
        "Retired kickoff marker: leftover process[] only; no live producer "
        "(team_preview_required/resolved are RETIRED_EVENT_TYPE_VALUES)."
    ),
}

PRODUCER_ORPHAN_ALLOWLIST: dict[str, str] = {}
