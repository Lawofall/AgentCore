"""OCI fragments for Chromium inside the cloud-desk guest.

Browser is not a second runsc jail: Playwright's bundle and a Chromium-sized
``/tmp`` are merged into the desk OCI (``gvisor._build_desk_oci``).
"""

from __future__ import annotations

import os

CHROMIUM_TMPFS_SIZE = "512m"


def playwright_browsers_mount(browsers_path: str) -> dict | None:
    """Ro-bind Playwright's Chromium tree when it exists on the host."""
    if not browsers_path or not os.path.isdir(browsers_path):
        return None
    return {
        "destination": browsers_path,
        "type": "bind",
        "source": browsers_path,
        "options": ["ro", "rbind", "nosuid", "nodev"],
    }
