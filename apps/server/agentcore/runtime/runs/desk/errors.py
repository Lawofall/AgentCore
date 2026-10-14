"""Prepare-time failure when a worker desk cannot be opened."""


class TargetDesktopError(Exception):
    """Unknown folder, unreachable desk, or a local root that is not ready."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
