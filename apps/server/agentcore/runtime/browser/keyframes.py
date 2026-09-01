"""Keyframe budget — per-turn frame count cap + monotonic filenames (D5 关键帧).

Every state-changing browser action (and ``screenshot``) drops a jpeg keyframe into
the workspace ``browser/`` dir; its path rides that step's ``tool_use_end.display``
(DURABLE → journal → replayable card). Two caps guard against a runaway loop
flooding the workspace / journal:

- **per-turn count** (default 60): over it, stop capturing but keep the tool working;
- **single-frame bytes** (default 512KB): an oversized frame is skipped, not written.

"Turn" is scoped to the driving ``run_id`` (CEO captain or worker). The count
resets when a new run drives the session. Browser is CEO+worker.
Filenames use a session-monotonic sequence so frames never overwrite each other.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class KeyframeTracker:
    """Per-conversation keyframe accounting (lives beside the session in the registry)."""

    seq: int = 0
    run_id: str = ""
    run_count: int = 0

    def should_capture(self, run_id: str, max_per_turn: int) -> bool:
        """Whether a keyframe may be captured now (resets the count on a new run)."""
        if run_id != self.run_id:
            self.run_id = run_id
            self.run_count = 0
        return self.run_count < max_per_turn

    def next_path(self) -> str:
        """Reserve the next frame slot and return its workspace-relative path."""
        self.seq += 1
        self.run_count += 1
        return f"browser/step-{self.seq:04d}.jpg"
