"""Optional turnOutcome comparison sidecar on conformance fixtures.

Not a third UI arbiter. Probe vectors (``turn_verdict_*``) get a *partial*
envelope of wire-grounded / already-documented host facts so both frontends'
native ``lib/turnOutcome`` outputs can be diffed by the existing harness.

Golden host values follow the landed product rule (协作图 UX: 有团队图时条是主判决).
Hint names are taken from the folded process (failed tool rows), not from either
end's copy.
"""

from __future__ import annotations

from typing import Any


def project_turn_verdict(name: str, projected: dict[str, Any]) -> dict[str, Any] | None:
    if name == "turn_verdict_team_host":
        return {
            "hasTeamStrip": True,
            "supportPackHost": "strip",
        }
    if name == "turn_verdict_unproductive_body_tool":
        names = [
            step["tool_name"]
            for step in projected.get("process") or []
            if isinstance(step, dict)
            and step.get("kind") == "tool"
            and step.get("status") == "error"
            and step.get("tool_name")
        ]
        return {"failedToolHintNames": names}
    return None
