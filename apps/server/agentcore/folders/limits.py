"""Resource caps for cloud-folder collaboration desks (abuse floor, not billing)."""

from __future__ import annotations

# Max members (accepted + pending) per folder, including the owner.
DEFAULT_MAX_MEMBERS_PER_FOLDER = 20
# Invite sends per user per rolling window.
DEFAULT_INVITE_RATE_MAX = 20
DEFAULT_INVITE_RATE_WINDOW_SECONDS = 3600
